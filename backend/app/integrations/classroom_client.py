"""Google Classroom API client wrapper for ClassMail J.

Extracts active courses, coursework (assignments), announcements, and
student submission states. Never renders HTML, never fetches external link
content, and never downloads attachments.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

import app.config


class ClassroomAuthError(Exception):
    """Raised when Google Classroom OAuth token is missing, revoked, or expired."""
    pass


class ClassroomCourse(BaseModel):
    id: str
    name: str
    section: Optional[str] = None
    alternate_link: Optional[str] = None
    course_state: str = "ACTIVE"


class ClassroomCourseWork(BaseModel):
    id: str
    course_id: str
    title: str
    description: Optional[str] = None
    state: str = "PUBLISHED"
    alternate_link: Optional[str] = None
    creation_time: Optional[str] = None
    update_time: Optional[str] = None
    due_date: Optional[Dict[str, int]] = None  # e.g. {"year": 2026, "month": 11, "day": 13}
    due_time: Optional[Dict[str, int]] = None  # e.g. {"hours": 17, "minutes": 0, "seconds": 0}
    materials: List[str] = Field(default_factory=list)  # http/https URLs
    submission_state: Optional[str] = None  # e.g. "TURNED_IN", "NEW", "CREATED", "RETURNED"


class ClassroomAnnouncement(BaseModel):
    id: str
    course_id: str
    text: str
    alternate_link: Optional[str] = None
    creation_time: Optional[str] = None
    update_time: Optional[str] = None
    materials: List[str] = Field(default_factory=list)  # http/https URLs


def _extract_material_urls(materials_raw: Optional[List[Dict[str, Any]]]) -> List[str]:
    """Extract only safe http/https URLs from Classroom materials."""
    urls: List[str] = []
    if not materials_raw:
        return urls

    for mat in materials_raw:
        if not isinstance(mat, dict):
            continue
        # Link attachment
        if "link" in mat and isinstance(mat["link"], dict):
            u = mat["link"].get("url")
            if u and isinstance(u, str) and u.startswith(("http://", "https://")):
                urls.append(u)
        # Drive file attachment
        if "driveFile" in mat and isinstance(mat["driveFile"], dict):
            df = mat["driveFile"].get("driveFile")
            if isinstance(df, dict):
                alt = df.get("alternateLink")
                if alt and isinstance(alt, str) and alt.startswith(("http://", "https://")):
                    urls.append(alt)
        # YouTube video attachment
        if "youTubeVideo" in mat and isinstance(mat["youTubeVideo"], dict):
            yt = mat["youTubeVideo"].get("alternateLink")
            if yt and isinstance(yt, str) and yt.startswith(("http://", "https://")):
                urls.append(yt)
        # Form attachment
        if "form" in mat and isinstance(mat["form"], dict):
            fu = mat["form"].get("formUrl")
            if fu and isinstance(fu, str) and fu.startswith(("http://", "https://")):
                urls.append(fu)

    return urls


class BaseClassroomClient(ABC):
    """Abstract base for Google Classroom client, easily mockable in tests."""

    @abstractmethod
    def list_courses(self) -> List[ClassroomCourse]:
        pass

    @abstractmethod
    def list_course_work(self, course_id: str) -> List[ClassroomCourseWork]:
        pass

    @abstractmethod
    def list_announcements(self, course_id: str) -> List[ClassroomAnnouncement]:
        pass

    @abstractmethod
    def get_student_submission_state(self, course_id: str, course_work_id: str) -> Optional[str]:
        pass


class ClassroomClient(BaseClassroomClient):
    """Production Google Classroom API client using authorized user token."""

    def __init__(self, token_path: Optional[str] = None) -> None:
        self.token_path = Path(token_path or app.config.CLASSROOM_TOKEN_FILE)
        self._service: Any = None

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service

        if not self.token_path.exists():
            raise ClassroomAuthError(
                f"Google Classroom token file not found at {self.token_path}. "
                "Please run `python -m app.integrations.google_auth classroom` to authenticate."
            )

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(self.token_path))
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self.token_path.write_text(creds.to_json(), encoding="utf-8")

            if not creds.valid:
                raise ClassroomAuthError(
                    "Google Classroom credentials are invalid or expired. "
                    "Please run `python -m app.integrations.google_auth classroom` to re-authenticate."
                )

            self._service = build("classroom", "v1", credentials=creds, cache_discovery=False)
            return self._service
        except ClassroomAuthError:
            raise
        except Exception as e:
            raise ClassroomAuthError(f"Failed to initialize Google Classroom API client: {e}")

    def list_courses(self) -> List[ClassroomCourse]:
        service = self._get_service()
        courses: List[ClassroomCourse] = []
        page_token: Optional[str] = None

        while True:
            try:
                res = service.courses().list(
                    courseStates=["ACTIVE"],
                    pageSize=50,
                    pageToken=page_token,
                ).execute()
            except Exception as e:
                raise RuntimeError(f"Error querying Classroom courses: {e}")

            for c in res.get("courses", []):
                courses.append(
                    ClassroomCourse(
                        id=c["id"],
                        name=c.get("name", "Untitled Course"),
                        section=c.get("section"),
                        alternate_link=c.get("alternateLink"),
                        course_state=c.get("courseState", "ACTIVE"),
                    )
                )

            page_token = res.get("nextPageToken")
            if not page_token:
                break

        return courses

    def list_course_work(self, course_id: str) -> List[ClassroomCourseWork]:
        service = self._get_service()
        items: List[ClassroomCourseWork] = []
        page_token: Optional[str] = None

        while True:
            try:
                res = service.courses().courseWork().list(
                    courseId=course_id,
                    courseWorkStates=["PUBLISHED"],
                    pageSize=50,
                    pageToken=page_token,
                ).execute()
            except Exception as e:
                raise RuntimeError(f"Error fetching coursework for course {course_id}: {e}")

            for cw in res.get("courseWork", []):
                cw_id = cw["id"]
                sub_state = self.get_student_submission_state(course_id, cw_id)
                mat_urls = _extract_material_urls(cw.get("materials"))

                items.append(
                    ClassroomCourseWork(
                        id=cw_id,
                        course_id=course_id,
                        title=cw.get("title", "Untitled Assignment"),
                        description=cw.get("description"),
                        state=cw.get("state", "PUBLISHED"),
                        alternate_link=cw.get("alternateLink"),
                        creation_time=cw.get("creationTime"),
                        update_time=cw.get("updateTime"),
                        due_date=cw.get("dueDate"),
                        due_time=cw.get("dueTime"),
                        materials=mat_urls,
                        submission_state=sub_state,
                    )
                )

            page_token = res.get("nextPageToken")
            if not page_token:
                break

        return items

    def list_announcements(self, course_id: str) -> List[ClassroomAnnouncement]:
        service = self._get_service()
        announcements: List[ClassroomAnnouncement] = []
        page_token: Optional[str] = None

        while True:
            try:
                res = service.courses().announcements().list(
                    courseId=course_id,
                    announcementStates=["PUBLISHED"],
                    pageSize=50,
                    pageToken=page_token,
                ).execute()
            except Exception as e:
                raise RuntimeError(f"Error fetching announcements for course {course_id}: {e}")

            for ann in res.get("announcements", []):
                ann_id = ann["id"]
                mat_urls = _extract_material_urls(ann.get("materials"))
                announcements.append(
                    ClassroomAnnouncement(
                        id=ann_id,
                        course_id=course_id,
                        text=ann.get("text", ""),
                        alternate_link=ann.get("alternateLink"),
                        creation_time=ann.get("creationTime"),
                        update_time=ann.get("updateTime"),
                        materials=mat_urls,
                    )
                )

            page_token = res.get("nextPageToken")
            if not page_token:
                break

        return announcements

    def get_student_submission_state(self, course_id: str, course_work_id: str) -> Optional[str]:
        service = self._get_service()
        try:
            res = service.courses().courseWork().studentSubmissions().list(
                courseId=course_id,
                courseWorkId=course_work_id,
                userId="me",
            ).execute()
            submissions = res.get("studentSubmissions", [])
            if submissions and isinstance(submissions, list):
                return submissions[0].get("state")
        except Exception:
            # Per-courseWork submission state permissions might be restricted; fail gracefully
            pass
        return None


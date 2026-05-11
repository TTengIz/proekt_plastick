from pydantic import BaseModel, Field
from datetime import datetime, date
from typing import Optional, List
from enum import Enum


class RoleEnum(str, Enum):
    user = "user"
    admin = "admin"


class AbsenceType(str, Enum):
    sick = "sick"
    vacation = "vacation"


# Регистрация
class UserCreate(BaseModel):
    login: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=72)  # ← ограничение
    role: RoleEnum = RoleEnum.user
    admin_secret: Optional[str] = None


# Токен
class Token(BaseModel):
    access_token: str
    token_type: str
    role: str  # ← добавляем роль в ответ


# Смены
class ShiftResponse(BaseModel):
    id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_hours: Optional[float] = None
    is_late: Optional[bool] = None  # для пользователя
    late_minutes: Optional[int] = None

    model_config = {"from_attributes": True}


# Календарь для админа
class CalendarDayUser(BaseModel):
    user_id: int
    login: str
    status: str  # working | late | absent | sick | vacation | free
    shift_start: Optional[datetime] = None
    late_minutes: Optional[int] = None
    absence_type: Optional[str] = None


class CalendarResponse(BaseModel):
    date: date
    users: List[CalendarDayUser]


# Отсутствие
class AbsenceCreate(BaseModel):
    user_id: int
    absence_type: AbsenceType
    start_date: date
    end_date: date
    comment: Optional[str] = None


class AbsenceResponse(BaseModel):
    id: int
    user_id: int
    absence_type: str
    start_date: date
    end_date: date
    comment: Optional[str] = None
    model_config = {"from_attributes": True}
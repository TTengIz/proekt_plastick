from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from datetime import datetime, timezone, date, timedelta, time as dt_time
from typing import Annotated, List
import io
import pandas as pd

from database import Base, engine, get_db
from models import User, Shift, Absence
from schemas import (
    UserCreate, Token, ShiftResponse,
    CalendarResponse, CalendarDayUser,
    AbsenceCreate, AbsenceResponse, RoleEnum, AbsenceType
)
from auth import (
    get_password_hash, verify_password,
    create_access_token, get_current_user, require_role
)

app = FastAPI(title="Учёт смен + Роли")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене укажи конкретные домены
    allow_credentials=True,
    allow_methods=["*"],  # Разрешаем все HTTP методы
    allow_headers=["*"],  # Разрешаем все заголовки
)

# Константы для логики опозданий
SHIFT_START_TIME = dt_time(9, 0)  # 9:00
GRACE_PERIOD_MINUTES = 15  # до 9:15 — не опоздание


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ==========================================
#  АВТОРИЗАЦИЯ
# ==========================================

@app.post("/auth/register", status_code=201)
async def register(user: UserCreate, db: AsyncSession = Depends(get_db)):
    try:
        existing_user = (await db.execute(select(User).where(User.login == user.login))).scalar_one_or_none()
        if existing_user:
            raise HTTPException(status_code=400, detail="Логин уже занят")

        role_value = user.role.value if hasattr(user.role, 'value') else str(user.role)

        if role_value == "admin" and user.admin_secret != "SUPER_SECRET_ADMIN_KEY_2026":
            raise HTTPException(status_code=403, detail="Неверный секрет администратора")

        db_user = User(
            login=user.login,
            password_hash=get_password_hash(user.password),
            role=role_value
        )
        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)
        return {"message": "Пользователь создан", "id": db_user.id, "role": db_user.role}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Ошибка регистрации: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@app.post("/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.login == form_data.username)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    token = create_access_token(data={"sub": user.login, "role": user.role})
    return {"access_token": token, "token_type": "bearer", "role": user.role}


# ==========================================
#  СМЕНЫ (ЛОГИКА)
# ==========================================

def calculate_lateness(shift_start: datetime) -> tuple[bool, int]:
    """Возвращает (опоздал?, минут опоздания)"""
    shift_date = shift_start.date()
    ideal_start = datetime.combine(shift_date, SHIFT_START_TIME, tzinfo=timezone.utc)
    grace_end = ideal_start + timedelta(minutes=GRACE_PERIOD_MINUTES)

    if shift_start <= grace_end:
        return False, 0
    late_minutes = int((shift_start - grace_end).total_seconds() / 60)
    return True, late_minutes


@app.post("/shifts/open", response_model=ShiftResponse)
async def open_shift(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Shift).where(Shift.user_id == current_user.id, Shift.end_time == None)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Смена уже открыта")

    new_shift = Shift(user_id=current_user.id, start_time=datetime.now(timezone.utc))
    db.add(new_shift)
    await db.commit()
    await db.refresh(new_shift)

    is_late, late_min = calculate_lateness(new_shift.start_time)
    return ShiftResponse(
        **new_shift.__dict__,
        is_late=is_late,
        late_minutes=late_min if is_late else None
    )


@app.post("/shifts/close", response_model=ShiftResponse)
async def close_shift(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Shift).where(Shift.user_id == current_user.id, Shift.end_time == None)
    active_shift = (await db.execute(stmt)).scalar_one_or_none()
    if not active_shift:
        raise HTTPException(status_code=400, detail="Нет активной смены")

    end_time = datetime.now(timezone.utc)
    active_shift.end_time = end_time
    active_shift.duration_hours = round((end_time - active_shift.start_time).total_seconds() / 3600, 2)

    await db.commit()
    await db.refresh(active_shift)
    return active_shift


@app.get("/shifts/history", response_model=List[ShiftResponse])
async def get_shift_history(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        limit: int = Query(50, description="Сколько записей показать")
):
    """История смен для текущего пользователя"""
    stmt = (
        select(Shift)
        .where(Shift.user_id == current_user.id)
        .order_by(Shift.start_time.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    shifts = result.scalars().all()

    response_list = []
    for shift in shifts:
        is_late, late_min = calculate_lateness(shift.start_time)

        shift_data = shift.__dict__.copy()
        shift_data.pop('_sa_instance_state', None)

        shift_data['is_late'] = is_late
        shift_data['late_minutes'] = late_min if is_late else 0

        response_list.append(ShiftResponse(**shift_data))

    return response_list


# ==========================================
#  КАЛЕНДАРЬ И ОТСУТСТВИЯ (АДМИН)
# ==========================================

@app.get("/admin/calendar/{target_date}", response_model=CalendarResponse)
async def get_calendar(
        target_date: date,
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db)
):
    """Возвращает статус всех пользователей на указанную дату"""
    users = (await db.execute(select(User))).scalars().all()

    calendar_users = []
    for user in users:
        status = "free"
        shift_start = None
        late_minutes = None
        absence_type = None

        absence_stmt = select(Absence).where(
            Absence.user_id == user.id,
            Absence.start_date <= target_date,
            Absence.end_date >= target_date
        )
        absence = (await db.execute(absence_stmt)).scalar_one_or_none()
        if absence:
            status = absence.absence_type
            absence_type = absence.absence_type
        else:
            shift_stmt = select(Shift).where(
                Shift.user_id == user.id,
                Shift.start_time >= datetime.combine(target_date, dt_time(0, 0), tzinfo=timezone.utc),
                Shift.start_time < datetime.combine(target_date + timedelta(days=1), dt_time(0, 0), tzinfo=timezone.utc)
            )
            shifts = (await db.execute(shift_stmt)).scalars().all()

            for shift in shifts:
                if shift.end_time is None:
                    status = "working"
                    shift_start = shift.start_time
                    is_late, late_min = calculate_lateness(shift.start_time)
                    if is_late:
                        status = "late"
                        late_minutes = late_min
                    break
                else:
                    is_late, late_min = calculate_lateness(shift.start_time)
                    if is_late and status == "free":
                        status = "late"
                        late_minutes = late_min
                        shift_start = shift.start_time

        calendar_users.append(CalendarDayUser(
            user_id=user.id,
            login=user.login,
            status=status,
            shift_start=shift_start,
            late_minutes=late_minutes,
            absence_type=absence_type
        ))

    return CalendarResponse(date=target_date, users=calendar_users)


@app.post("/admin/absences", response_model=AbsenceResponse, status_code=201)
async def create_absence(
        absence_data: AbsenceCreate,
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db)
):
    """Админ оформляет больничный или отпуск сотруднику"""
    user_stmt = select(User).where(User.id == absence_data.user_id)
    result = await db.execute(user_stmt)
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    absence_type_str = absence_data.absence_type.value if hasattr(absence_data.absence_type,
                                                                  'value') else absence_data.absence_type

    new_absence = Absence(
        user_id=absence_data.user_id,
        absence_type=absence_type_str,
        start_date=absence_data.start_date,
        end_date=absence_data.end_date,
        comment=absence_data.comment,
        created_by=current_user.id
    )

    db.add(new_absence)
    await db.commit()
    await db.refresh(new_absence)
    return new_absence


# ==========================================
#  ОТЧЕТЫ И ДАШБОРД (НОВОЕ!)
# ==========================================

@app.get("/admin/reports/shifts/excel")
async def export_shifts_to_excel(
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db),
        start_date: str = Query(..., description="Дата начала (YYYY-MM-DD)"),
        end_date: str = Query(..., description="Дата конца (YYYY-MM-DD)")
):
    """Экспорт всех смен за период в Excel файл"""

    # Парсим даты и добавляем таймзону UTC, чтобы сравнение работало корректно
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(hour=0, minute=0, second=0, tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)

    stmt = (
        select(Shift, User.login)
        .join(User, Shift.user_id == User.id)
        .where(
            Shift.start_time >= start_dt,
            Shift.start_time <= end_dt,
            Shift.end_time.isnot(None)
        )
        .order_by(Shift.start_time)
    )

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        raise HTTPException(status_code=404, detail="Нет данных за выбранный период")

    data = []
    for shift, login in rows:
        is_late, late_min = calculate_lateness(shift.start_time)
        data.append({
            "Сотрудник": login,
            "Начало смены": shift.start_time.strftime("%Y-%m-%d %H:%M"),
            "Конец смены": shift.end_time.strftime("%Y-%m-%d %H:%M"),
            "Отработано (часов)": shift.duration_hours,
            "Опоздание (мин)": late_min,
            "Статус": "Опоздал" if is_late else "Вовремя"
        })

    df = pd.DataFrame(data)
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Смены")

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=shifts_report_{start_date}_{end_date}.xlsx"}
    )


@app.get("/admin/dashboard")
async def get_admin_dashboard(
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db)
):
    """Статистика для администратора"""

    # Всего сотрудников (роль user)
    total_users_stmt = select(func.count(User.id)).where(User.role == "user")
    total_users_count = (await db.execute(total_users_stmt)).scalar_one()

    # Сейчас на смене
    active_shifts_stmt = select(func.count(Shift.id)).where(Shift.end_time.is_(None))
    active_count = (await db.execute(active_shifts_stmt)).scalar_one()

    # Опоздавшие сегодня
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)

    # Берем все закрытые смены за сегодня
    late_stmt = select(Shift.id).where(
        Shift.start_time >= today_start,
        Shift.end_time.isnot(None)
    )
    today_shifts_ids = (await db.execute(late_stmt)).scalars().all()

    late_count = 0
    for shift_id in today_shifts_ids:
        stmt_check = select(Shift.start_time).where(Shift.id == shift_id)
        start_time = (await db.execute(stmt_check)).scalar_one()
        is_late, _ = calculate_lateness(start_time)
        if is_late:
            late_count += 1

    return {
        "total_users": total_users_count,
        "currently_working": active_count,
        "late_today": late_count,
        "date": datetime.now(timezone.utc).date()
    }


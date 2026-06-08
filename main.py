from fastapi import FastAPI, Depends, HTTPException, status, Query, Body
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, delete
from datetime import datetime, timezone, date, timedelta, time as dt_time
from typing import Annotated, List, Optional
import io
import pandas as pd

from database import Base, engine, get_db
# Модели
from models import User, Shift, Absence, Blank, ProductionLog, FinishedProduct, RawMaterial

# Схемы
from schemas import (
    RoleEnum, AbsenceType,
    UserCreate, Token, ShiftResponse,
    CalendarResponse, CalendarDayUser,
    AbsenceCreate, AbsenceResponse,
    RawMaterialCreate, RawMaterialResponse, TakeMaterialRequest,
    FinishedProductCreate, FinishedProductResponse, TakeProductRequest,
    BlankCreate, BlankResponse, TakeBlankRequest,
    ProductionReportCreate, ProductionLogResponse, PaymentUpdate
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
        # Проверяем, не занят ли логин
        existing_user = (await db.execute(select(User).where(User.login == user.login))).scalar_one_or_none()
        if existing_user:
            raise HTTPException(status_code=400, detail="Логин уже занят")

        # Получаем строковое значение роли
        if hasattr(user.role, 'value'):
            role_value = user.role.value
        else:
            role_value = str(user.role)

        print(f"📝 Регистрация: login={user.login}, role={role_value}, secret={user.admin_secret}")

        # Проверяем секретные ключи
        if role_value == "admin":
            if user.admin_secret != "SUPER_SECRET_ADMIN_KEY_2026":
                print(f" Неверный секрет админа: {user.admin_secret}")
                raise HTTPException(status_code=403, detail="Неверный секрет администратора")
        elif role_value == "manager":
            if user.admin_secret != "BOSS_MANAGER_KEY_2026":
                print(f" Неверный секрет руководителя: {user.admin_secret}")
                raise HTTPException(status_code=403, detail="Неверный секрет руководителя")
        else:
            # Для обычных сотрудников принудительно ставим "user"
            role_value = "user"

        # Создаём пользователя
        db_user = User(
            login=user.login,
            password_hash=get_password_hash(user.password),
            role=role_value  # <-- Важно: сохраняем именно role_value
        )

        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)

        print(f" Пользователь создан: id={db_user.id}, role={db_user.role}")

        return {
            "message": "Пользователь создан",
            "id": db_user.id,
            "role": db_user.role
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f" Ошибка регистрации: {e}")
        import traceback
        traceback.print_exc()
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
#  КАЛЕНДАРЬ ОТПУСКА И ОТСУТСТВИЯ (АДМИН)
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
#  ОТЧЕТЫ И ДАШБОРД
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


@app.get("/admin/shifts/all-history")
async def admin_get_all_shifts(
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db),
        limit: int = 100
):
    """Админ видит историю смен ВСЕХ сотрудников"""
    stmt = select(Shift).order_by(Shift.start_time.desc()).limit(limit)
    result = await db.execute(stmt)
    shifts = result.scalars().all()

    # Формируем ответ с информацией о сотрудниках
    result_with_users = []
    for shift in shifts:
        user_stmt = select(User).where(User.id == shift.user_id)
        user = (await db.execute(user_stmt)).scalar_one_or_none()

        shift_data = {
            "id": shift.id,
            "user_login": user.login if user else "Unknown",
            "start_time": shift.start_time,
            "end_time": shift.end_time,
            "duration_hours": shift.duration_hours if hasattr(shift, 'duration_hours') else None,
            "is_late": shift.is_late if hasattr(shift, 'is_late') else False,
            "late_minutes": shift.late_minutes if hasattr(shift, 'late_minutes') else 0
        }
        result_with_users.append(shift_data)

    return result_with_users

# --- СКЛАД СЫРЬЯ (ТОЛЬКО АДМИН) ---

@app.post("/admin/warehouse/raw/add", response_model=RawMaterialResponse, status_code=201)
async def add_raw_material(
        data: RawMaterialCreate,
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db)
):
    # Проверяем, есть ли уже такой материал с такими же характеристиками
    stmt = select(RawMaterial).where(
        RawMaterial.name == data.name,
        RawMaterial.thickness == data.thickness,
        RawMaterial.color == data.color
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing:
        # Если есть - увеличиваем количество
        existing.quantity += data.quantity
        await db.commit()
        await db.refresh(existing)
        return existing

    # Иначе создаём новый
    new_mat = RawMaterial(
        name=data.name,
        thickness=data.thickness,
        color=data.color,
        quantity=data.quantity
    )
    db.add(new_mat)
    await db.commit()
    await db.refresh(new_mat)
    return new_mat


@app.post("/warehouse/raw/add", status_code=200)
async def add_raw_material(
        data: RawMaterialCreate,  # или dict
        current_user: Annotated[User, Depends(require_role(RoleEnum.manager))],
        db: AsyncSession = Depends(get_db)
):
    """Добавить сырье на склад (приход)"""
    # Проверяем существует ли такое сырье
    stmt = select(RawMaterial).where(RawMaterial.name == data.name)
    raw = (await db.execute(stmt)).scalar_one_or_none()

    if raw:
        # Обновляем количество
        raw.quantity += data.quantity
        raw.last_updated = datetime.now()
    else:
        # Создаем новое
        raw = RawMaterial(
            name=data.name,
            quantity=data.quantity,
            unit=data.unit
        )
        db.add(raw)

    await db.commit()
    return {"message": "Сырье добавлено", "quantity": raw.quantity}


@app.get("/admin/warehouse/raw", response_model=List[RawMaterialResponse])
async def get_raw_materials(
    current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
    db: AsyncSession = Depends(get_db)
):
    """Просмотр списка сырья (только админ)"""
    result = await db.execute(select(RawMaterial))
    return result.scalars().all()


@app.post("/admin/warehouse/raw/take")
async def take_raw_material(
        data: TakeMaterialRequest,
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db)
):
    """Админ берёт сырьё для фрезеровки"""
    stmt = select(RawMaterial).where(RawMaterial.id == data.material_id)
    mat = (await db.execute(stmt)).scalar_one_or_none()

    if not mat:
        raise HTTPException(status_code=404, detail="Material not found")
    if mat.quantity < data.amount:
        raise HTTPException(status_code=400, detail="Insufficient quantity")

    mat.quantity -= data.amount
    await db.commit()
    return {"message": "Material taken successfully", "remaining": mat.quantity}

# ==========================================
# 2. СКЛАД ЗАГОТОВОК (Blanks)
# ==========================================

# Админ добавляет заготовки (после фрезеровки сырья)
@app.post("/admin/warehouse/blanks/add", response_model=BlankResponse, status_code=201)
async def add_blanks(
        data: BlankCreate,
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db)
):
    # Если такая заготовка уже есть, плюсуем количество
    stmt = select(Blank).where(Blank.name == data.name)
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing:
        existing.quantity += data.quantity
        await db.commit()
        await db.refresh(existing)
        return existing

    new_blank = Blank(name=data.name, quantity=data.quantity)
    db.add(new_blank)
    await db.commit()
    await db.refresh(new_blank)
    return new_blank


# Взять заготовки (может любой авторизованный пользователь)
@app.post("/warehouse/blanks/take")
async def take_blanks(
        data: TakeBlankRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    stmt = select(Blank).where(Blank.id == data.blank_id)
    blank = (await db.execute(stmt)).scalar_one_or_none()

    if not blank:
        raise HTTPException(status_code=404, detail="Blank not found")
    if blank.quantity < data.amount:
        raise HTTPException(status_code=400, detail="Not enough blanks in stock")

    blank.quantity -= data.amount
    await db.commit()
    return {"message": "Blanks taken", "remaining": blank.quantity}


# Просмотр склада заготовок
@app.get("/warehouse/blanks", response_model=List[BlankResponse])
async def get_blanks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Blank))
    return result.scalars().all()


# ==========================================
# 3. ОТЧЕТ О ПРОИЗВОДСТВЕ (Production Log)
# ==========================================

@app.post("/user/production/report", response_model=ProductionLogResponse)
async def submit_production_report(  # <-- async def обязательно!
    report: ProductionReportCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Пользователь сдает работу:
    1. Проверяет, что взял заготовок >= сделал изделий + брак
    2. Если недостача - требует причину
    3. Сохраняет отчёт в БД и текстовый файл
    """

    # Проверяем логику: blanks_taken должно быть >= items_produced + defect_amount
    total_output = report.items_produced + report.defect_amount

    if total_output > report.blanks_taken:
        raise HTTPException(
            status_code=400,
            detail=f"Нельзя сделать больше изделий ({total_output}), чем взято заготовок ({report.blanks_taken})"
        )

    # Если есть недостача (взяли больше, чем сдали), требуем причину
    shortage = report.blanks_taken - total_output

    if shortage > 0 and not report.defect_reason:
        raise HTTPException(
            status_code=400,
            detail=f"Недостача {shortage} заготовок! Укажите причину в поле defect_reason"
        )

    # Создаём запись в журнале
    new_log = ProductionLog(
        user_id=current_user.id,
        blank_id=report.blank_id,
        blanks_taken=report.blanks_taken,
        items_produced=report.items_produced,
        defect_amount=report.defect_amount,
        defect_reason=report.defect_reason,
        product_name=report.product_name,
        created_at=datetime.now()
    )

    db.add(new_log)
    await db.commit()  # <-- await здесь ОК, потому что функция async
    await db.refresh(new_log)

    # Вызов синхронной функции БЕЗ await:
    save_report_to_file(new_log, current_user.login, shortage)

    return new_log


def save_report_to_file(log: ProductionLog, user_login: str, shortage: int):
    """Сохраняет отчёт в текстовый файл (синхронная функция!)"""
    import os

    reports_dir = "production_reports"
    if not os.path.exists(reports_dir):
        os.makedirs(reports_dir)

    filename = f"{log.created_at.strftime('%Y-%m-%d')}_{user_login}.txt"
    filepath = os.path.join(reports_dir, filename)

    report_text = f"""
====================================
ОТЧЁТ О ПРОИЗВОДСТВЕ
====================================
Дата и время: {log.created_at.strftime('%d.%m.%Y %H:%M')}
Сотрудник: {user_login}
ID записи: {log.id}
------------------------------------
Взято заготовок: {log.blanks_taken} шт.
Изготовлено годных: {log.items_produced} шт.
Брак: {log.defect_amount} шт.
------------------------------------
Итого сдано: {log.items_produced + log.defect_amount} шт.
Недостача: {shortage} шт.
------------------------------------
"""
    if shortage > 0 and log.defect_reason:
        report_text += f"ПРИЧИНА НЕДОСТАЧИ:\n{log.defect_reason}\n"
    if log.product_name:
        report_text += f"Готовое изделие: {log.product_name}\n"

    report_text += "====================================\n\n"

    # Запись в файл (обычный синхронный код, без await!)
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(report_text)


# История пользователя (для личного кабинета)
@app.get("/user/production/history", response_model=List[ProductionLogResponse])
async def get_my_production_history(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        limit: int = Query(50, ge=1, le=500)
):
    stmt = (
        select(ProductionLog)
        .where(ProductionLog.user_id == current_user.id)
        .order_by(ProductionLog.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# История всех (только для админа)
@app.get("/admin/production/history", response_model=List[ProductionLogResponse])
async def get_all_production_history(
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db),
        user_id: Optional[int] = Query(None, description="Filter by specific user"),
        limit: int = Query(100, ge=1, le=1000)
):
    stmt = select(ProductionLog).order_by(ProductionLog.created_at.desc()).limit(limit)

    if user_id:
        stmt = stmt.where(ProductionLog.user_id == user_id)

    result = await db.execute(stmt)
    return result.scalars().all()


@app.get("/products/list")
async def get_products_list(
        current_user: Annotated[User, Depends(get_current_user)],
        db: AsyncSession = Depends(get_db)
):
    """Получить уникальные названия изделий из истории"""
    # Получаем уникальные названия изделий
    stmt = select(ProductionLog.product_name, ProductionLog.blank_id, Blank.name.label('blank_name')).join(
        Blank, ProductionLog.blank_id == Blank.id
    ).where(ProductionLog.product_name != None).distinct()

    result = await db.execute(stmt)
    products = result.all()

    return [
        {
            "id": i,
            "name": p.product_name,
            "blank_id": p.blank_id,
            "blank_name": p.blank_name
        }
        for i, p in enumerate(products)
    ]

# --- СКЛАД ГОТОВОЙ ПРОДУКЦИИ ---

@app.post("/admin/warehouse/finished/add", response_model=FinishedProductResponse, status_code=201)
async def add_finished_product(
        data: FinishedProductCreate,
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db)
):
    stmt = select(FinishedProduct).where(FinishedProduct.name == data.name)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        existing.quantity += data.quantity
        await db.commit()
        await db.refresh(existing)
        return existing

    new_prod = FinishedProduct(name=data.name, quantity=data.quantity)
    db.add(new_prod)
    await db.commit()
    await db.refresh(new_prod)
    return new_prod


@app.post("/warehouse/finished/take")
async def take_finished_product(
        data: TakeProductRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    stmt = select(FinishedProduct).where(FinishedProduct.id == data.product_id)
    prod = (await db.execute(stmt)).scalar_one_or_none()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")
    if prod.quantity < data.amount:
        raise HTTPException(status_code=400, detail="Insufficient quantity")

    prod.quantity -= data.amount
    await db.commit()
    return {"message": "Product taken successfully", "remaining": prod.quantity}


@app.get("/user/production/weekly-stats")
async def get_weekly_stats(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """Возвращает статистику за текущую неделю"""
    from datetime import datetime, timedelta

    # Определяем начало и конец текущей недели
    today = datetime.now().date()
    start_of_week = today - timedelta(days=today.weekday())  # Понедельник
    end_of_week = start_of_week + timedelta(days=6)  # Воскресенье

    start_datetime = datetime.combine(start_of_week, datetime.min.time())
    end_datetime = datetime.combine(end_of_week, datetime.max.time())

    # Получаем все записи за неделю
    stmt = select(ProductionLog).where(
        ProductionLog.user_id == current_user.id,
        ProductionLog.created_at >= start_datetime,
        ProductionLog.created_at <= end_datetime
    )

    result = await db.execute(stmt)
    logs = result.scalars().all()

    # Считаем статистику
    total_blanks_taken = sum(log.blanks_taken for log in logs)
    total_items_produced = sum(log.items_produced for log in logs)
    total_defects = sum(log.defect_amount for log in logs)
    total_shortage = sum(log.blanks_taken - (log.items_produced + log.defect_amount) for log in logs)

    return {
        "period": f"{start_of_week.strftime('%d.%m.%Y')} - {end_of_week.strftime('%d.%m.%Y')}",
        "total_records": len(logs),
        "total_blanks_taken": total_blanks_taken,
        "total_items_produced": total_items_produced,
        "total_defects": total_defects,
        "total_shortage": total_shortage,
        "logs": [
            {
                "id": log.id,
                "product_name": log.product_name,
                "blanks_taken": log.blanks_taken,
                "items_produced": log.items_produced,
                "defect_amount": log.defect_amount,
                "defect_reason": log.defect_reason,
                "created_at": log.created_at.strftime("%d.%m.%Y %H:%M")
            }
            for log in logs
        ]
    }


@app.get("/admin/production/all-reports")
async def get_all_reports(
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db),
        user_id: Optional[int] = Query(None, description="Filter by user ID"),
        limit: int = Query(100, ge=1, le=1000)
):
    """Получить все отчёты (только админ)"""
    stmt = select(ProductionLog).order_by(ProductionLog.created_at.desc()).limit(limit)

    if user_id:
        stmt = stmt.where(ProductionLog.user_id == user_id)

    result = await db.execute(stmt)
    logs = result.scalars().all()

    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "user_login": log.user.login if log.user else "Unknown",
            "blank_id": log.blank_id,
            "blanks_taken": log.blanks_taken,
            "items_produced": log.items_produced,
            "defect_amount": log.defect_amount,
            "defect_reason": log.defect_reason,
            "product_name": log.product_name,
            "created_at": log.created_at.strftime("%d.%m.%Y %H:%M")
        }
        for log in logs
    ]


# ==========================================
#  МОНИТОРИНГ ПЕРСОНАЛА (АДМИН)
# ==========================================

@app.get("/admin/monitoring/status")
async def get_monitoring_status(
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db),
        target_date: date = Query(default_factory=lambda: datetime.now().date())
):
    """Получить статус всех сотрудников на дату"""
    # Получаем всех пользователей
    users_stmt = select(User)
    users = (await db.execute(users_stmt)).scalars().all()

    result = []
    now = datetime.now()

    for user in users:
        user_data = {
            "id": user.id,
            "login": user.login,
            "role": user.role,
            "status": "no_shift",
            "start_time": None,
            "duration": None
        }

        # Проверяем absence (отпуск/больничный)
        absence_stmt = select(Absence).where(
            Absence.user_id == user.id,
            Absence.start_date <= target_date,
            Absence.end_date >= target_date
        )
        absence = (await db.execute(absence_stmt)).scalar_one_or_none()

        if absence:
            user_data["status"] = absence.absence_type  # sick, vacation
        else:
            # Проверяем активную смену
            shift_stmt = select(Shift).where(
                Shift.user_id == user.id,
                func.date(Shift.start_time) == target_date
            )
            shift = (await db.execute(shift_stmt)).scalar_one_or_none()

            if shift:
                user_data["start_time"] = shift.start_time.strftime("%H:%M")

                if shift.end_time:
                    # Смена завершена
                    duration = (shift.end_time - shift.start_time).total_seconds() / 3600
                    user_data["duration"] = f"{duration:.1f} ч"
                    user_data["status"] = "working"
                else:
                    # Смена активна - считаем длительность
                    duration = (now - shift.start_time).total_seconds() / 3600
                    user_data["duration"] = f"{duration:.1f} ч"

                    # Проверяем опоздание (если начал после 9:00)
                    if shift.start_time.hour >= 9 and shift.start_time.minute > 15:
                        user_data["status"] = "late"
                    else:
                        user_data["status"] = "working"
            else:
                # Нет смены - проверяем не опоздал ли
                user_data["status"] = "absent"

        result.append(user_data)

    return result

@app.post("/admin/monitoring/set_absence", status_code=200)
async def set_employee_absence(
        data: dict,
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db)
):
    user_id = data.get("user_id")
    start_date = datetime.strptime(data.get("start_date"), "%Y-%m-%d").date()
    end_date = datetime.strptime(data.get("end_date"), "%Y-%m-%d").date()
    absence_type = data.get("absence_type")  # "sick", "vacation" или None

    # Удаляем старые отсутствия, которые пересекаются с этим периодом
    del_stmt = delete(Absence).where(
        Absence.user_id == user_id,
        or_(
            and_(Absence.start_date <= start_date, Absence.end_date >= start_date),
            and_(Absence.start_date <= end_date, Absence.end_date >= end_date),
            and_(Absence.start_date >= start_date, Absence.end_date <= end_date)
        )
    )
    await db.execute(del_stmt)
    await db.commit()

    # Создаем новое отсутствие на период
    if absence_type in ["sick", "vacation"]:
        new_absence = Absence(
            user_id=user_id,
            absence_type=absence_type,
            start_date=start_date,
            end_date=end_date,
            comment=f"Установлено администратором: {start_date} - {end_date}"
        )
        db.add(new_absence)
        await db.commit()

    return {"message": "Status updated", "period": f"{start_date} - {end_date}"}


@app.get("/admin/monitoring/absences/{user_id}")
async def get_user_absences(
        user_id: int,
        current_user: Annotated[User, Depends(require_role(RoleEnum.admin))],
        db: AsyncSession = Depends(get_db),
        year: int = Query(default_factory=lambda: datetime.now().year)
):
    """Получить все отсутствия пользователя за год"""
    start_of_year = datetime(year, 1, 1).date()
    end_of_year = datetime(year, 12, 31).date()

    stmt = select(Absence).where(
        Absence.user_id == user_id,
        Absence.start_date <= end_of_year,
        Absence.end_date >= start_of_year
    )
    result = await db.execute(stmt)
    absences = result.scalars().all()

    return [
        {
            "id": a.id,
            "absence_type": a.absence_type,
            "start_date": a.start_date.isoformat(),
            "end_date": a.end_date.isoformat(),
            "comment": a.comment
        }
        for a in absences
    ]


@app.post("/manager/users/payment", status_code=200)
async def set_user_payment(
        data: dict,
        current_user: Annotated[User, Depends(require_role(RoleEnum.manager))],
        db: AsyncSession = Depends(get_db)
):
    user_id = data.get("user_id")
    payment_method = data.get("payment_method")
    payment_rate = data.get("payment_rate", 0)  # <-- ВАЖНО!

    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    user.payment_method = payment_method
    user.payment_rate = payment_rate if payment_rate else None  # <-- СОХРАНЯЕМ СТАВКУ

    await db.commit()
    await db.refresh(user)

    return {
        "message": "Система оплаты обновлена",
        "user_login": user.login,
        "payment_method": user.payment_method,
        "payment_rate": user.payment_rate  # <-- ВОЗВРАЩАЕМ
    }


@app.get("/manager/users/list")
async def get_all_users_for_payment(
        current_user: Annotated[User, Depends(require_role(RoleEnum.manager))],
        db: AsyncSession = Depends(get_db)
):
    stmt = select(User).where(User.role != "manager").order_by(User.id)
    result = await db.execute(stmt)
    users = result.scalars().all()

    return [
        {
            "id": u.id,
            "login": u.login,
            "role": u.role,
            "payment_method": u.payment_method or "Не указан",
            "payment_rate": u.payment_rate or 0
        }
        for u in users
    ]

# --- МЕНЕДЖЕР ---

@app.get("/manager/payroll/calculate")
async def calculate_payroll(
        current_user: Annotated[User, Depends(require_role(RoleEnum.manager))],
        db: AsyncSession = Depends(get_db),
        start_date: date = Query(...),
        end_date: date = Query(...)
):
    print(f" РАСЧЕТ ЗАРПЛАТЫ: Период {start_date} - {end_date}")

    users_stmt = select(User).where(User.role != "manager")
    users = (await db.execute(users_stmt)).scalars().all()

    payroll_report = []

    # Границы периода: начало дня start_date и КОНЕЦ дня end_date (23:59:59)
    start_dt = datetime.combine(start_date, dt_time(0, 0))
    end_dt = datetime.combine(end_date, dt_time(23, 59, 59))

    print(f"⏰ Диапазон запроса к БД: с {start_dt} по {end_dt}")

    for user in users:
        if not user.payment_method or user.payment_method == "Не указан" or not user.payment_rate:
            print(f"🚫 Пропуск {user.login}: нет ставки или системы оплаты")
            continue

        daily_earnings = {}
        total_earnings = 0.0

        try:
            if user.payment_method == "Почасовая":
                shifts_stmt = select(Shift).where(
                    Shift.user_id == user.id,
                    Shift.start_time >= start_dt,
                    Shift.start_time <= end_dt
                )
                shifts = (await db.execute(shifts_stmt)).scalars().all()
                print(f"🕒 {user.login} (Почасовая): найдено {len(shifts)} смен")

                for shift in shifts:
                    shift_date = shift.start_time.date().isoformat()
                    if shift.end_time:
                        duration = (shift.end_time - shift.start_time).total_seconds() / 3600
                    else:
                        duration = (datetime.now() - shift.start_time).total_seconds() / 3600

                    earnings = duration * user.payment_rate
                    daily_earnings[shift_date] = daily_earnings.get(shift_date, 0) + earnings
                    total_earnings += earnings

            elif user.payment_method == "Сдельная":
                logs_stmt = select(ProductionLog).where(
                    ProductionLog.user_id == user.id,
                    ProductionLog.created_at >= start_dt,
                    ProductionLog.created_at <= end_dt
                )
                logs = (await db.execute(logs_stmt)).scalars().all()
                print(f"📦 {user.login} (Сдельная): найдено {len(logs)} отчетов")

                for log in logs:
                    log_date = log.created_at.date().isoformat()
                    # Считаем только годные изделия
                    earnings = log.items_produced * user.payment_rate
                    daily_earnings[log_date] = daily_earnings.get(log_date, 0) + earnings
                    total_earnings += earnings

            elif user.payment_method == "Оклад + премия":
                current = start_date
                while current <= end_date:
                    daily_earnings[current.isoformat()] = user.payment_rate
                    total_earnings += user.payment_rate
                    current += timedelta(days=1)

        except Exception as e:
            print(f"❌ Ошибка при расчете для {user.login}: {e}")
            import traceback
            traceback.print_exc()

        if daily_earnings:
            print(f"✅ {user.login}: начислено {total_earnings:.2f} руб.")
            payroll_report.append({
                "user_id": user.id,
                "login": user.login,
                "payment_method": user.payment_method,
                "rate": user.payment_rate,
                "daily": daily_earnings,
                "total": round(total_earnings, 2)
            })

    return {
        "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "employees": payroll_report
    }


@app.get("/manager/monitoring/status")
async def manager_get_monitoring_status(
    # Разрешаем и admin, и manager
    current_user: Annotated[User, Depends(require_role(RoleEnum.admin, RoleEnum.manager))],
    db: AsyncSession = Depends(get_db),
    target_date: date = Query(default_factory=lambda: datetime.now().date())
):
    """Мониторинг статуса сотрудников (только для Менеджера)"""
    # 1. Берем всех сотрудников (кроме менеджеров)
    users_stmt = select(User).where(User.role.in_(["user", "admin"]))
    users = (await db.execute(users_stmt)).scalars().all()

    result = []
    now = datetime.now()

    for user in users:
        status_info = {
            "id": user.id,
            "login": user.login,
            "status": "absent",
            "start_time": None,
            "duration": None
        }

        # 2. Проверяем, нет ли больничного/отпуска
        absence_stmt = select(Absence).where(
            Absence.user_id == user.id,
            Absence.start_date <= target_date,
            Absence.end_date >= target_date
        )
        absence = (await db.execute(absence_stmt)).scalar_one_or_none()

        if absence:
            status_info["status"] = absence.absence_type  # sick, vacation
        else:
            # 3. Если нет absence, ищем смену за сегодня
            shift_stmt = select(Shift).where(
                Shift.user_id == user.id,
                func.date(Shift.start_time) == target_date
            )
            shift = (await db.execute(shift_stmt)).scalar_one_or_none()

            if shift:
                status_info["start_time"] = shift.start_time.strftime("%H:%M")

                if shift.end_time:
                    # Смена закрыта
                    dur = (shift.end_time - shift.start_time).total_seconds() / 3600
                    status_info["duration"] = f"{dur:.1f} ч"
                    status_info["status"] = "working"  # Работал
                else:
                    # Смена открыта (прямо сейчас работает)
                    dur = (now - shift.start_time).total_seconds() / 3600
                    status_info["duration"] = f"{dur:.1f} ч"

                    # Проверка на опоздание (например, начало позже 09:15)
                    if shift.start_time.hour == 9 and shift.start_time.minute > 15:
                        status_info["status"] = "late"
                    elif shift.start_time.hour > 9:
                        status_info["status"] = "late"
                    else:
                        status_info["status"] = "working"
            else:
                # Смены нет, отпуска нет -> Не вышел
                status_info["status"] = "absent"

        result.append(status_info)

    return result


# 1. Сброс пароля
@app.put("/manager/users/{user_id}/password", status_code=200)
async def reset_user_password(
        user_id: int,
        data: dict,  # {"new_password": "123"}
        current_user: Annotated[User, Depends(require_role(RoleEnum.manager))],
        db: AsyncSession = Depends(get_db)
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Нельзя сбросить пароль самому себе")

    new_password = data.get("new_password")
    if not new_password:
        raise HTTPException(status_code=400, detail="Пароль пустой")

    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    user.password_hash = get_password_hash(new_password)
    await db.commit()
    return {"message": "Пароль изменен", "login": user.login}


# 2. Удаление пользователя
@app.delete("/manager/users/{user_id}", status_code=200)
async def delete_user(
        user_id: int,
        current_user: Annotated[User, Depends(require_role(RoleEnum.manager))],
        db: AsyncSession = Depends(get_db)
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")

    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    await db.delete(user)
    await db.commit()
    return {"message": "Пользователь удален", "login": user.login}

# Создаём папки если нет
Path("static").mkdir(exist_ok=True)
Path("static/css").mkdir(exist_ok=True)
Path("static/js").mkdir(exist_ok=True)

# Раздаём статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

# Главная страница
@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")

@app.get("/dashboard")
async def dashboard_page():
    return FileResponse("static/dashboard.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
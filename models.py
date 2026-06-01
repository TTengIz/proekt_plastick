from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Date, func
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime, timezone


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    login = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(512), nullable=False)
    role = Column(String(20), default="user", nullable=False)
    payment_method = Column(String, nullable=True, default="Не указан")
    payment_rate = Column(Float, nullable=True, default=0)
    created_at = Column(DateTime, server_default=func.now())

    shifts = relationship("Shift", back_populates="user", cascade="all, delete-orphan")
    absences = relationship("Absence", foreign_keys="Absence.user_id",
                            back_populates="user", cascade="all, delete-orphan")


class Shift(Base):
    __tablename__ = "shifts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    duration_hours = Column(Float, nullable=True)
    user = relationship("User", back_populates="shifts")


class Absence(Base):
    __tablename__ = "absences"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    absence_type = Column(String(20), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    comment = Column(String(200), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"))

    # Два отношения к User:
    # 1. user — кто отсутствует
    # 2. creator — кто оформил
    user = relationship("User", foreign_keys=[user_id], back_populates="absences")
    creator = relationship("User", foreign_keys=[created_by])


class RawMaterial(Base):
    __tablename__ = "raw_materials"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)  # Например: "ПЭТ"
    thickness = Column(String(50), nullable=False)  # Например: "3 мм"
    color = Column(String(50), nullable=False)  # Например: "прозрачный"
    quantity = Column(Float, nullable=False, default=0.0)

class FinishedProduct(Base):
    __tablename__ = "finished_products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    quantity = Column(Integer, nullable=False, default=0)


# --- НОВАЯ МОДЕЛЬ: ЗАГОТОВКИ ---
class Blank(Base):
    __tablename__ = "blanks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)  # Например: "Заготовка корпуса А"
    quantity = Column(Integer, nullable=False, default=0)


# --- ОБНОВЛЕННАЯ МОДЕЛЬ: ЖУРНАЛ ПРОИЗВОДСТВА ---
# Добавляем причины брака и связь с заготовками
class ProductionLog(Base):
    __tablename__ = "production_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Что взяли со склада заготовок
    blank_id = Column(Integer, ForeignKey("blanks.id"), nullable=False)
    blanks_taken = Column(Integer, nullable=False)  # Сколько заготовок взяли

    # Результаты работы
    items_produced = Column(Integer, nullable=False, default=0)  # Сколько годных сделали
    defect_amount = Column(Integer, nullable=False, default=0)  # Сколько брака

    # Причина брака (опционально)
    defect_reason = Column(String(200), nullable=True)  # "Брак заготовки" или "Ошибка при сборке"

    # Ссылка на итоговое изделие (если нужно)
    product_name = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Связи
    user = relationship("User", backref="production_logs")
    blank = relationship("Blank", backref="production_logs")
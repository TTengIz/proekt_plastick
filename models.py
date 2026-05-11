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
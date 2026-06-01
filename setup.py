from cx_Freeze import setup, Executable

setup(
    name="ShiftApp",
    version="1.0",
    description="Учёт смен и производства",
    executables=[
        Executable(
            "desktop_app.py",
            target_name="ShiftApp.exe",
            base="gui"  # <-- Исправлено!
        )
    ],
    options={
        "build_exe": {
            "packages": ["fastapi", "uvicorn", "sqlalchemy", "pydantic", "requests", "pandas"],
            "include_files": [
                "main.py",
                "models.py",
                "schemas.py",
                "auth.py",
                "database.py",
            ]
        }
    }
)
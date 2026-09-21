from datetime import datetime
from sqlalchemy import String, DateTime, Boolean, Integer, func
from sqlalchemy.orm import Mapped, mapped_column
from backend.db.database import Base


class AppTeamMember(Base):
    """
    Membri del team dell'applicazione con ruoli specifici.
    Questi sono utenti (già autorizzati nel Portal) a cui vengono
    assegnati ruoli interni all'applicazione.
    """
    __tablename__ = "app_team_members"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)  # UUID dal Portal
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=True)
    app_role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")  # user, operator, admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=True)  # Per multi-tenancy
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    added_by: Mapped[str] = mapped_column(String(255), nullable=True)  # Email di chi ha aggiunto

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "app_role": self.app_role,
            "is_active": self.is_active,
            "tenant_id": self.tenant_id,
            "added_at": self.added_at.isoformat() if self.added_at else None,
            "added_by": self.added_by,
        }

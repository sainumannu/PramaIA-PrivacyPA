from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from backend.db.database import Base


class LicenseLog(Base):
    """Log delle verifiche licenza per audit."""
    
    __tablename__ = "license_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(String, nullable=True)
    check_code = Column(String, nullable=False, index=True)
    message = Column(String, nullable=False)
    fingerprint = Column(String, nullable=True)
    checked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    def to_dict(self):
        return {
            "id": self.id,
            "license_id": self.license_id,
            "check_code": self.check_code,
            "message": self.message,
            "fingerprint": self.fingerprint,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }

from fastapi import Depends
from sqlalchemy.orm import Session

from docflow.db.session import get_db

DbSession = Depends(get_db)


def db_session(db: Session = Depends(get_db)) -> Session:
    return db

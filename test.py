from app.db.session import SessionLocal
from app.models.user import User

db = SessionLocal()
users = db.query(User).all()
if not users:
    print("No users found.")
else:
    for user in users:
        print(f"ID: {user.id}, Username: {user.username}, Role: {user.role}")
db.close()
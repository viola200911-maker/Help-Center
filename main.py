from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, Depends, HTTPException, status, BackgroundTasks, Form, File, UploadFile
from fastapi.staticfiles import StaticFiles
import logging
import os
from fastapi.templating import Jinja2Templates
from typing import List, Optional, Dict
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jwt import PyJWTError, decode, encode
import time
from enum import Enum
from datetime import datetime
import json
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, select
from sqlalchemy.orm import relationship, joinedload
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import bcrypt

import os

# Програма спочатку шукає асинхронний URL у змінних Render (DATABASE_URL)
# Якщо не знаходить (наприклад, коли ви тестуєте локально на комп'ютері),
# використовує локальну базу даних або ваш зовнішній лінк як запасний варіант
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://help_center_user:HXCYsCc2XS2d6PkFWy1OFLAtnYTU5uIXV@dpg-d9ck4oe7r5hc738nbqog-a.oregon-postgres.render.com/help_center"
)

engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

app = FastAPI()

os.makedirs("static/user_problem_image", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl='token')
templates = Jinja2Templates(directory='templates')

class UserRole(str, Enum):
    USER = 'user'
    TECHNICIAN = 'technician'
    ADMIN = 'admin'

class ProblemStatus(str, Enum):
    PENDING = 'Чекає на обробку'
    PROCESSING = 'В обробці'
    COMPLETED = 'Завершений'

class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, index=True)
    password = Column(String(100))
    email = Column(String(100), unique=True, index=True)
    role = Column(String(50), default=UserRole.USER)

    problems = relationship("Problem", foreign_keys="[Problem.user_id]", back_populates="user")
    assigned_problems = relationship("Problem", foreign_keys="[Problem.admin_id]", back_populates="admin")
    messages = relationship("ProblemMessage", back_populates="sender")


class Problem(Base):
    __tablename__ = "problems"

    id = Column(Integer, primary_key=True)
    title = Column(String(100))
    description = Column(Text)
    date_created = Column(DateTime(timezone=True), server_default=func.now())
    image_url = Column(Text, nullable=True)
    status = Column(String(50), default=ProblemStatus.PENDING)

    user_id = Column(Integer, ForeignKey("users.user_id"))
    admin_id = Column(Integer, ForeignKey("users.user_id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id], back_populates="problems")
    admin = relationship("User", foreign_keys=[admin_id], back_populates="assigned_problems")
    messages = relationship("ProblemMessage", back_populates="problem", cascade="all, delete-orphan")


class ProblemMessage(Base):
    __tablename__ = "problem_messages"

    id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    date_sent = Column(DateTime(timezone=True), server_default=func.now())
    read = Column(Boolean, default=False)

    sender_id = Column(Integer, ForeignKey("users.user_id"))
    problem_id = Column(Integer, ForeignKey("problems.id"))

    sender = relationship("User", back_populates="messages")
    problem = relationship("Problem", back_populates="messages")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, stored_password: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), stored_password.encode('utf-8'))
    except (ValueError, TypeError):
        return password == stored_password


async def create_default_users():
    async with async_session() as db:
        result = await db.execute(select(User).where(User.username.in_(["admin1", "tech1"])))
        if not result.scalars().all():
            db.add_all([
                User(
                    username="admin1",
                    password=hash_password("admin1"),
                    email="admin@gmail.com",
                    role=UserRole.ADMIN
                ),
                User(
                    username="tech1",
                    password=hash_password("tech1"),
                    email="tech@gmail.com",
                    role=UserRole.TECHNICIAN
                )
            ])
            await db.commit()

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.on_event("startup")
async def on_startup():
    await init_db()
    await create_default_users()

async def get_user_by_id(id: int):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.user_id == id))
        user = result.scalar_one_or_none()
        if user:
            return {"user_id": user.user_id, "username": user.username, "email": user.email, "password": user.password, "role": user.role}
        return None

async def get_user_by_email(email: str):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            return {"user_id": user.user_id, "username": user.username, "email": user.email, "password": user.password, "role": user.role}
        return None   
    
async def get_user_by_username(username: str):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user:
            return {"user_id": user.user_id, "username": user.username, "email": user.email, "password": user.password, "role": user.role}
        return None

async def create_user(username: str, email: str, password: str, role: str = UserRole.USER):
    async with async_session() as session:
        user = User(username=username, email=email, password=password, role=role)
        session.add(user)
        await session.commit()

def validate_password(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Пароль повинен містити мінімум 8 символів"
    if not any(c.isupper() for c in password):
        return False, "Пароль повинен містити хоча б одну велику літеру"
    if not any(c.islower() for c in password):
        return False, "Пароль повинен містити хоча б одну малу літеру"
    if not any(c.isdigit() for c in password):
        return False, "Пароль повинен містити хоча б одну цифру"
    return True, ""

def validate_username(username: str) -> tuple[bool, str]:
    if len(username) < 3:
        return False, "Ім'я користувача повинно містити мінімум 3 символи"
    if len(username) > 20:
        return False, "Ім'я користувача повинно містити максимум 20 символів"
    if not username.replace('_', '').isalnum():
        return False, "Ім'я користувача може містити лише літери, цифри та підкреслення"
    return True, ""


@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name='index.html')

@app.post('/register')
async def register(
    request: Request, 
    email: str = Form(...), 
    username: str = Form(...), 
    password: str = Form(...)
):
    valid_username, username_error = validate_username(username)
    if not valid_username:
        return JSONResponse(status_code=400, content={"detail": username_error})
        
    valid_password, password_error = validate_password(password)
    if not valid_password:
        return JSONResponse(status_code=400, content={"detail": password_error})
        
    if await get_user_by_email(email):
        return JSONResponse(status_code=400, content={"detail": "Користувач з таким емайлом вже є"})
        
    if await get_user_by_username(username):
       return JSONResponse(status_code=400, content={"detail": "Користувач з таким ім'ям вже є"})
       
    hashed_password = hash_password(password)
    await create_user(username, email, hashed_password, 'user')
    return {"detail": "Success"}

@app.get("/login")
async def login_page(request: Request, error: str = None):
    return templates.TemplateResponse(request=request, name='index.html', context={"error": error})
  
@app.post('/token')
async def login(response: Response, username: str = Form(...), password: str = Form(...)):
    header = {'alg': 'HS256', 'typ': 'JWT'}
    user = await get_user_by_username(username)
    
    if not user:
        return JSONResponse(status_code=400, content={"detail": "Користувача не існує"})
    
    if not verify_password(password, user['password']):
        return JSONResponse(status_code=400, content={"detail": "Невірний пароль"})

    role = user['role']
    user_id = user['user_id']
    payload = {
        'user_id': user_id,
        'username': username,
        'role': role,
        'exp': int(time.time() + 86400)
    }
    
    encoded_jwt = encode(payload, 'SECRET_KEY_0987654321abcdefghijklmnopqrstuvwxyz', algorithm='HS256', headers=header)
    response.set_cookie(key='access_token', value=encoded_jwt, max_age=86400, samesite='lax', httponly=False)
    return {'access_token': encoded_jwt, "token_type": 'bearer'}


@app.post('/create_problem')
async def create_problem(request: Request, title: str = Form(...), description: str = Form(...), file: UploadFile = File(None)):
    token = request.cookies.get('access_token')
    if not token:
        return JSONResponse(status_code=401, content={"detail": "Токен не знайдено"})
        
    try:
        user_id, username, role = await get_current_user_from_token(token)
        img_path = None
        if file and file.filename:
            filename = f"{int(time.time())}_{file.filename}"
            file_location = f"user_problem_image/{filename}"
            with open('static/' + file_location, "wb+") as f:
                f.write(await file.read())
            img_path = file_location
            
        async with async_session() as session:
            problem = Problem(title=title, description=description, user_id=user_id, image_url=img_path)
            session.add(problem)
            await session.commit()
            await session.refresh(problem)
            problem_id = problem.id
            
        return {"problem_id": problem_id}
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})


@app.get('/problems')
async def get_problems(request: Request):
    token = request.cookies.get('access_token')
    if not token: return []
    try:
        user_id, username, role = await get_current_user_from_token(token)
        async with async_session() as session:
            base_query = select(Problem).options(joinedload(Problem.user), joinedload(Problem.admin)).order_by(Problem.id.desc())
            
            if role == 'admin': 
                result = await session.execute(base_query)
            elif role == 'technician': 
                result = await session.execute(base_query.where(Problem.admin_id == user_id))
            else: 
                result = await session.execute(base_query.where(Problem.user_id == user_id))
                
            problems = result.scalars().all()
            problems_list = []
            
            for p in problems:
                problems_list.append({
                    "id": p.id,
                    "title": p.title,
                    "description": p.description,
                    "status": p.status,
                    "username": p.user.username if p.user else None,
                    "admin_username": p.admin.username if p.admin else None,
                    "image_url": p.image_url
                })
            return problems_list
    except Exception: 
        return []
    

@app.get('/available_problems')
async def get_available_problems(request: Request):
    token = request.cookies.get('access_token')
    if not token: return []
    try:
        user_id, username, role = await get_current_user_from_token(token)
        if role not in ['technician', 'admin']:
            return []
            
        async with async_session() as session:
            result = await session.execute(
                select(Problem).options(joinedload(Problem.user)).where(Problem.admin_id == None).order_by(Problem.id.desc())
            )
            problems = result.scalars().all()
            problems_list = []
            
            for p in problems:
                problems_list.append({
                    "id": p.id,
                    "title": p.title,
                    "description": p.description,
                    "status": p.status,
                    "username": p.user.username if p.user else None,
                    "image_url": p.image_url
                })
            return problems_list
    except Exception:
        return []

@app.post('/assign_problem')
async def assign_problem(request: Request, problem_id: int = Form(...)):
    token = request.cookies.get('access_token')
    if not token: return JSONResponse(status_code=401, content={"detail": "Токен не знайдено"})
    try:
        user_id, username, role = await get_current_user_from_token(token)
        if role not in ['technician', 'admin']:
            return JSONResponse(status_code=403, content={"detail": "Тільки технік або адмін може призначати проблеми"})
        async with async_session() as session:
            problem = await session.execute(select(Problem).where(Problem.id == problem_id))
            problem_obj = problem.scalar_one_or_none()
            if not problem_obj:
                return JSONResponse(status_code=404, content={"detail": "Проблему не знайдено"})
            problem_obj.admin_id = user_id
            problem_obj.status = ProblemStatus.PROCESSING
            await session.commit()
            
            await manager.broadcast(json.dumps({
                "type": "system_event",
                "event": "assign_changed",
                "admin_username": username
            }), problem_id=str(problem_id))
            
        return {"detail": "Проблему призначено"}
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

@app.post('/change_status')
async def change_status(request: Request, problem_id: int = Form(...), status: str = Form(...)):
    token = request.cookies.get('access_token')
    if not token: return JSONResponse(status_code=401, content={"detail": "Токен не знайдено"})
    try:
        user_id, username, role = await get_current_user_from_token(token)
        if role not in ['technician', 'admin']:
            return JSONResponse(status_code=403, content={"detail": "Тільки технік або адмін може змінювати статус"})
        async with async_session() as session:
            problem = await session.execute(select(Problem).where(Problem.id == problem_id))
            problem_obj = problem.scalar_one_or_none()
            if not problem_obj:
                return JSONResponse(status_code=404, content={"detail": "Проблему не знайдено"})
            if role == 'technician' and problem_obj.admin_id != user_id:
                return JSONResponse(status_code=403, content={"detail": "Ви не призначені до цієї проблеми"})
            problem_obj.status = status
            await session.commit()
            
            await manager.broadcast(json.dumps({
                "type": "system_event",
                "event": "status_changed",
                "status": status
            }), problem_id=str(problem_id))
            
        return {"detail": "Статус змінено"}
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

@app.post('/delete_problem')
async def delete_problem(request: Request, problem_id: int = Form(...)):
    token = request.cookies.get('access_token')
    if not token: return JSONResponse(status_code=401, content={"detail": "Токен не знайдено"})
    try:
        user_id, username, role = await get_current_user_from_token(token)
        async with async_session() as session:
            problem = await session.execute(select(Problem).where(Problem.id == problem_id))
            problem_obj = problem.scalar_one_or_none()
            if not problem_obj:
                return JSONResponse(status_code=404, content={"detail": "Проблему не знайдено"})
            if role != 'admin' and problem_obj.user_id != user_id:
                return JSONResponse(status_code=403, content={"detail": "Ви можете видаляти тільки свої проблеми"})
            await session.delete(problem_obj)
            await session.commit()
        return {"detail": "Проблему видалено"}
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

@app.get('/problem/{problem_id}')
async def get_problem_details(request: Request, problem_id: int):
    token = request.cookies.get('access_token')
    if not token: return {}
    try:
        user_id, username, role = await get_current_user_from_token(token)
        async with async_session() as session:
            problem = await session.execute(select(Problem).where(Problem.id == problem_id))
            problem_obj = problem.scalar_one_or_none()
            if not problem_obj:
                return {}
            
            # Перевірка безпеки IDOR на рівні бекенду
            if role == 'user' and problem_obj.user_id != user_id:
                raise HTTPException(status_code=403, detail="Немає доступу до цієї проблеми")

            user = await session.execute(select(User).where(User.user_id == problem_obj.user_id))
            user_obj = user.scalar_one_or_none()
            
            admin_obj = None
            if problem_obj.admin_id:
                admin = await session.execute(select(User).where(User.user_id == problem_obj.admin_id))
                admin_obj = admin.scalar_one_or_none()
            
            messages = await session.execute(
                select(ProblemMessage)
                .where(ProblemMessage.problem_id == problem_id)
                .order_by(ProblemMessage.date_sent)
            )
            
            messages_list = []
            for msg in messages.scalars().all():
                sender = await session.execute(select(User).where(User.user_id == msg.sender_id))
                sender_obj = sender.scalar_one_or_none()
                messages_list.append({
                    "id": msg.id,
                    "text": msg.text,
                    "sender_id": msg.sender_id,
                    "sender_username": sender_obj.username if sender_obj else None,
                    "date_sent": msg.date_sent.isoformat(),
                    "read": msg.read
                })
                
            return {
                "id": problem_obj.id,
                "title": problem_obj.title,
                "description": problem_obj.description,
                "status": problem_obj.status,
                "username": user_obj.username if user_obj else None,
                "admin_username": admin_obj.username if admin_obj else None,
                "image_url": problem_obj.image_url,
                "messages": messages_list
            }
    except Exception as e:
        print(f"Error: {e}")
        return {}

@app.post('/mark_read')
async def mark_messages_as_read(request: Request, problem_id: int = Form(...)):
    token = request.cookies.get('access_token')
    if not token: return JSONResponse(status_code=401, content={"detail": "Токен не знайдено"})
    try:
        user_id, username, role = await get_current_user_from_token(token)
        async with async_session() as session:
            result = await session.execute(
                select(ProblemMessage).where(
                    ProblemMessage.problem_id == problem_id,
                    ProblemMessage.sender_id != user_id,
                    ProblemMessage.read == False
                )
            )
            messages = result.scalars().all()
            for m in messages:
                m.read = True
            await session.commit()
            
            await manager.broadcast(json.dumps({
                "type": "system_event",
                "event": "messages_read"
            }), problem_id=str(problem_id))
            
        return {"detail": "Повідомлення прочитано"}
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

@app.post('/change_username')
async def change_username(request: Request, new_username: str = Form(...)):
    token = request.cookies.get('access_token')
    if not token: return JSONResponse(status_code=401, content={"detail": "Токен не знайдено"})
    try:
        user_id, username, role = await get_current_user_from_token(token)
        is_valid, error_msg = validate_username(new_username)
        if not is_valid: return JSONResponse(status_code=400, content={"detail": error_msg})
        
        async with async_session() as session:
            existing = await session.execute(select(User).where(User.username == new_username))
            if existing.scalar_one_or_none():
                return JSONResponse(status_code=400, content={"detail": "Користувач з таким ім'ям вже існує"})
            user = await session.execute(select(User).where(User.user_id == user_id))
            user_obj = user.scalar_one_or_none()
            if not user_obj: return JSONResponse(status_code=404, content={"detail": "Користувача не знайдено"})
            user_obj.username = new_username
            await session.commit()
        return {"detail": "Ім'я змінено"}
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

@app.post('/change_password')
async def change_password(request: Request, new_password: str = Form(...)):
    token = request.cookies.get('access_token')
    if not token: return JSONResponse(status_code=401, content={"detail": "Токен не знайдено"})
    try:
        user_id, username, role = await get_current_user_from_token(token)
        is_valid, error_msg = validate_password(new_password)
        if not is_valid: return JSONResponse(status_code=400, content={"detail": error_msg})
        
        async with async_session() as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            user_obj = user.scalar_one_or_none()
            if not user_obj: return JSONResponse(status_code=404, content={"detail": "Користувача не знайдено"})
            user_obj.password = hash_password(new_password)
            await session.commit()
        return {"detail": "Пароль змінено"}
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

@app.get('/users')
async def get_users(request: Request):
    token = request.cookies.get('access_token')
    if not token: return []
    try:
        user_id, username, role = await get_current_user_from_token(token)
        if role != 'admin': return []
        async with async_session() as session:
            users = await session.execute(select(User).order_by(User.user_id.desc()))
            users_list = []
            for u in users.scalars().all():
                users_list.append({
                    "user_id": u.user_id,
                    "username": u.username,
                    "email": u.email,
                    "role": u.role
                })
            return users_list
    except Exception:
        return []

@app.post('/change_user_role')
async def change_user_role(request: Request, target_username: str = Form(...), new_role: str = Form(...)):
    token = request.cookies.get('access_token')
    if not token: return JSONResponse(status_code=401, content={"detail": "Токен не знайдено"})
    try:
        user_id, username, role = await get_current_user_from_token(token)
        if role != 'admin': return JSONResponse(status_code=403, content={"detail": "Тільки адмін може змінювати ролі"})
        async with async_session() as session:
            target_user = await session.execute(select(User).where(User.username == target_username))
            target_obj = target_user.scalar_one_or_none()
            if not target_obj: return JSONResponse(status_code=404, content={"detail": "Користувача не знайдено"})
            if new_role not in ['user', 'technician', 'admin']: return JSONResponse(status_code=400, content={"detail": "Невірна роль"})
            target_obj.role = new_role
            await session.commit()
        return {"detail": "Роль успішно змінено"}
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

@app.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Ви вийшли з системи"}

async def get_current_user_from_token(token: str):
    try:
        payload = decode(token, "SECRET_KEY_0987654321abcdefghijklmnopqrstuvwxyz", algorithms=["HS256"])
        username = payload.get("username")
        role = payload.get('role')
        user_id = payload.get('user_id')
        timeexp = payload.get('exp')
        if username is None:
            raise ValueError("Недійсний токен")
        if int(time.time()) > timeexp:
            raise ValueError("Термін дії токена закінчився")
        user = await get_user_by_username(username)
        if not user:
            raise ValueError("Користувача не знайдено")
        return user["user_id"], username, user["role"]
    except PyJWTError:
        raise ValueError("Не вдалося розшифрувати токен")


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Dict[WebSocket, tuple]] = {}

    async def connect(self, websocket: WebSocket, username: str, user_id: int, problem_id: str):
        await websocket.accept()
        if problem_id not in self.active_connections:
            self.active_connections[problem_id] = {}
        self.active_connections[problem_id][websocket] = (username, user_id)

    def disconnect(self, websocket: WebSocket, problem_id: str):
        if problem_id in self.active_connections:
            if websocket in self.active_connections[problem_id]:
                del self.active_connections[problem_id][websocket]
            if not self.active_connections[problem_id]:
                del self.active_connections[problem_id]

    def get_room_count(self, problem_id: str) -> int:
        if problem_id in self.active_connections:
            return len(self.active_connections[problem_id])
        return 0

    async def broadcast(self, message: str, problem_id: str):
        disconnected = []
        if problem_id in self.active_connections:
            for connection in self.active_connections[problem_id]:
                try:
                    await connection.send_text(message)
                except Exception:
                    disconnected.append(connection)
            for conn in disconnected:
                self.disconnect(conn, problem_id)

manager = ConnectionManager()

@app.get('/')
async def home(request: Request):
    return templates.TemplateResponse(request=request, name='index.html')

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = None, problem_id: str = None):
    if not token or not problem_id:
        await websocket.close(code=1008, reason="Відсутні параметри")
        return
        
    try:
        user_id, username, role = await get_current_user_from_token(token)
        await manager.connect(websocket, username, user_id, problem_id=problem_id)
        
        try:
            while True:
                data = await websocket.receive_text()
                
                # Якщо в чат-кімнаті онлайн більше ніж 1 людина, повідомлення одразу прочитане
                is_read_instantly = manager.get_room_count(problem_id) > 1
                
                async with async_session() as session:
                    message = ProblemMessage(text=data, sender_id=user_id, problem_id=int(problem_id), read=is_read_instantly)
                    session.add(message)
                    await session.commit()
                    await session.refresh(message)
                    
                message_json = json.dumps({
                    "type": "chat_message",
                    "username": username,
                    "sender_id": user_id,
                    "text": data, 
                    "timestamp": datetime.now().isoformat(), 
                    "read": is_read_instantly
                })
                await manager.broadcast(message_json, problem_id=problem_id)
                
        except WebSocketDisconnect:
            manager.disconnect(websocket, problem_id=problem_id)
            
    except ValueError as e:
        await websocket.close(code=1008, reason=str(e))

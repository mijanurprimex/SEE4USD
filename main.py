import sqlite3
import hashlib
import jwt
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

SECRET_KEY = "SEE4USD_SUPER_SECRET_KEY_CHANGE_IN_PRODUCTION"
ALGORITHM = "HS256"
DB_FILE = "see4usd.db"

app = FastAPI(title="SEE4USD Premium API Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://see4usd-22k8.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Database Initialization ---
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Users Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                balance_usd REAL DEFAULT 0.0,
                pending_usd REAL DEFAULT 0.0,
                country TEXT DEFAULT 'US',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Transactions & History Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL, -- 'ad_view', 'task', 'deposit', 'withdrawal', 'admin_credit'
                amount REAL NOT NULL,
                status TEXT NOT NULL, -- 'completed', 'pending', 'rejected'
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        
        # Create default Admin if not exists (Username: admin, Password: adminpassword)
        admin_pass = hashlib.sha256("adminpassword".encode()).hexdigest()
        cursor.execute("SELECT id FROM users WHERE username = 'admin'")
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO users (username, email, password_hash, role, balance_usd)
                VALUES ('admin', 'admin@see4usd.com', ?, 'admin', 0.0)
            """, (admin_pass,))
        conn.commit()

init_db()

# --- Pydantic Schemas ---
class RegisterSchema(BaseModel):
    username: str
    email: EmailStr
    password: str
    country: Optional[str] = "US"

class LoginSchema(BaseModel):
    identifier: str # Accepts Email or Username
    password: str

class DepositWithdrawSchema(BaseModel):
    amount: float
    method: str
    wallet_address: str

class AdminBalanceSchema(BaseModel):
    target_username: str
    amount: float
    type: str # 'add' or 'subtract'

class TaskSubmitSchema(BaseModel):
    task_id: str
    task_title: str
    reward: float

# --- Security Helpers ---
def hash_pwd(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def create_jwt(user_id: int, role: str) -> str:
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.utcnow() + timedelta(days=7)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authentication token")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

# --- Authentication Endpoints ---
@app.post("/api/v1/auth/register")
def register(data: RegisterSchema):
    pwd_hashed = hash_pwd(data.password)
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO users (username, email, password_hash, country)
                VALUES (?, ?, ?, ?)
            """, (data.username.lower(), data.email.lower(), pwd_hashed, data.country))
            conn.commit()
            return {"status": "success", "message": "Account created successfully. Please login."}
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Username or Email already registered.")

@app.post("/api/v1/auth/login")
def login(data: LoginSchema):
    pwd_hashed = hash_pwd(data.password)
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, username, role FROM users 
            WHERE (username = ? OR email = ?) AND password_hash = ?
        """, (data.identifier.lower(), data.identifier.lower(), pwd_hashed))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials.")
        
        token = create_jwt(user[0], user[2])
        return {
            "token": token,
            "user": {"id": user[0], "username": user[1], "role": user[2]}
        }

# --- User Profile & Ledger ---
@app.get("/api/v1/user/profile")
def get_profile(user: dict = Depends(get_current_user)):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, email, role, balance_usd, pending_usd, country, created_at FROM users WHERE id = ?", (user["user_id"],))
        u = cursor.fetchone()
        
        cursor.execute("SELECT type, amount, status, description, created_at FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user["user_id"],))
        history = [{"type": row[0], "amount": row[1], "status": row[2], "desc": row[3], "date": row[4]} for row in cursor.fetchall()]
        
        return {
            "id": u[0], "username": u[1], "email": u[2], "role": u[3],
            "balance_usd": u[4], "pending_usd": u[5], "country": u[6], "joined": u[7],
            "history": history
        }

# --- Task Submission Endpoint (Admin Approval Required) ---
@app.post("/api/v1/tasks/submit")
def submit_task(data: TaskSubmitSchema, user: dict = Depends(get_current_user)):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Add to pending balance
        cursor.execute("UPDATE users SET pending_usd = pending_usd + ? WHERE id = ?", (data.reward, user["user_id"]))
        # Log Transaction as Pending
        cursor.execute("""
            INSERT INTO transactions (user_id, type, amount, status, description)
            VALUES (?, 'task', ?, 'pending', ?)
        """, (user["user_id"], data.reward, f"Task: {data.task_title}"))
        conn.commit()
    return {"status": "success", "message": "Task submitted! Pending admin review and crediting."}

# --- Deposit & Withdrawal Endpoint (Admin Approval Required) ---
@app.post("/api/v1/finance/request")
def finance_request(data: DepositWithdrawSchema, type: str, user: dict = Depends(get_current_user)):
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid transaction amount")
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        desc = f"{type.capitalize()} via {data.method} ({data.wallet_address})"
        
        if type == "withdrawal":
            cursor.execute("SELECT balance_usd FROM users WHERE id = ?", (user["user_id"],))
            bal = cursor.fetchone()[0]
            if bal < data.amount:
                raise HTTPException(status_code=400, detail="Insufficient available balance")
            # Reserve funds into pending
            cursor.execute("UPDATE users SET balance_usd = balance_usd - ?, pending_usd = pending_usd + ? WHERE id = ?", (data.amount, data.amount, user["user_id"]))
        
        cursor.execute("""
            INSERT INTO transactions (user_id, type, amount, status, description)
            VALUES (?, ?, ?, 'pending', ?)
        """, (user["user_id"], type, data.amount, desc))
        conn.commit()
        
    return {"status": "success", "message": f"{type.capitalize()} request recorded. Awaiting admin review."}

# --- Admin Operations (Exclusively Controls Account Balances) ---
@app.post("/api/v1/admin/modify-balance")
def admin_modify_balance(data: AdminBalanceSchema, user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Unauthorized Access: Admin privileges required.")
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, balance_usd FROM users WHERE username = ?", (data.target_username.lower(),))
        target = cursor.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Target user profile not found.")
        
        amt = data.amount if data.type == "add" else -data.amount
        cursor.execute("UPDATE users SET balance_usd = balance_usd + ? WHERE id = ?", (amt, target[0]))
        cursor.execute("""
            INSERT INTO transactions (user_id, type, amount, status, description)
            VALUES (?, 'admin_credit', ?, 'completed', ?)
        """, (target[0], amt, f"Direct Balance adjustment by Admin"))
        conn.commit()
        
    return {"status": "success", "message": f"Successfully updated balance for user @{data.target_username}"}

@app.get("/api/v1/admin/pending-requests")
def get_pending_requests(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin Access Required")
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.id, u.username, t.type, t.amount, t.description, t.created_at 
            FROM transactions t JOIN users u ON t.user_id = u.id 
            WHERE t.status = 'pending' ORDER BY t.id DESC
        """)
        return [{"tx_id": r[0], "username": r[1], "type": r[2], "amount": r[3], "desc": r[4], "date": r[5]} for r in cursor.fetchall()]

@app.post("/api/v1/admin/approve-request")
def approve_request(tx_id: int, approve: bool, user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin Access Required")
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, type, amount, status FROM transactions WHERE id = ?", (tx_id,))
        tx = cursor.fetchone()
        if not tx or tx[3] != 'pending':
            raise HTTPException(status_code=400, detail="Transaction not pending")
        
        uid, tx_type, amt = tx[0], tx[1], tx[2]
        new_status = 'completed' if approve else 'rejected'
        
        if approve:
            if tx_type in ['task', 'deposit']:
                # Move pending to balance for approved deposits/tasks
                cursor.execute("UPDATE users SET balance_usd = balance_usd + ?, pending_usd = MAX(0, pending_usd - ?) WHERE id = ?", (amt, amt, uid))
            elif tx_type == 'withdrawal':
                # Deduct pending for approved withdrawal
                cursor.execute("UPDATE users SET pending_usd = MAX(0, pending_usd - ?) WHERE id = ?", (amt, uid))
        else: # Rejection logic
            if tx_type == 'withdrawal':
                # Refund reserved balance
                cursor.execute("UPDATE users SET balance_usd = balance_usd + ?, pending_usd = MAX(0, pending_usd - ?) WHERE id = ?", (amt, amt, uid))
            elif tx_type in ['task', 'deposit']:
                cursor.execute("UPDATE users SET pending_usd = MAX(0, pending_usd - ?) WHERE id = ?", (amt, uid))
        
        cursor.execute("UPDATE transactions SET status = ? WHERE id = ?", (new_status, tx_id))
        conn.commit()
        
    return {"status": "success", "message": f"Transaction status updated to {new_status}."}

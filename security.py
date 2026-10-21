import hashlib
import secrets
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError,InvalidHashError

# 密码处理
PasswordHasher = PasswordHasher()
#注册时：为密码生成带随机盐的安全哈希
def hash_password(password:str)->str:
    return PasswordHasher.hash(password)

#登录时：验证密码
def verify_password(password:str,hashed_password:str)->bool:
    try:
        PasswordHasher.verify(hashed_password,password)
        return True
    except (VerificationError,InvalidHashError):
        return False  


#令牌处理
#生成原始 Token 和对应的 SHA-256 哈希
def generate_session_token() -> tuple[str,str]:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return token,token_hash

#验证令牌
def hash_session_token(token:str)->str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

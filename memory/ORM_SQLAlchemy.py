from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column
from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime,ForeignKey, ForeignKeyConstraint, Identity, Index, Integer,String,func,Text, text

#1.创建异步引擎
ASYNC_DATABASE_URL = 'postgresql+asyncpg://postgres:123456@localhost:5432/travel_agent_db'
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_size=10,#设置连接池活跃数
    max_overflow=20,#设置额外的连接数
)

#2.定义模型类：基类+表对应的模型类
#创建基类
class Base(DeclarativeBase):
    pass
#供需要user_id的业务表复用
class UserIdFKMixin:
    user_id: Mapped[str] = mapped_column(String(64),ForeignKey('users.user_id',ondelete='CASCADE'),nullable=False,)

#users表
class Users(Base,):
    __tablename__ = 'users'
    user_id: Mapped[str] = mapped_column(String(64),primary_key=True,nullable=False,)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False,)

#用户旅行计划表
class Travel_Plans(Base,UserIdFKMixin):
    __tablename__ = 'travel_plans'
    plan_id: Mapped[int] = mapped_column(BigInteger,Identity(always=True),primary_key=True)
    city:Mapped[str] = mapped_column(String(50),nullable=False,)
    itinerary: Mapped[str] = mapped_column(Text,nullable=False,)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=True,)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=True,)
    __table_args__ = (
        CheckConstraint('start_time IS NULL OR end_time IS NULL OR end_time >= start_time', name='travel_plans_check'),
        Index('idx_travel_plans_user_id', 'user_id', 'city'),
    )

#供需要user_id的业务表复用
class UserIdPKMixin:
    user_id: Mapped[str] = mapped_column(String(64),ForeignKey('users.user_id',ondelete='CASCADE'),nullable=False,primary_key=True)


#用户喜好表
class User_Preferences(Base,UserIdPKMixin):
    __tablename__ = 'user_preferences'
    preference_key: Mapped[str] = mapped_column(String(50),nullable=False,primary_key=True,)
    preference_value: Mapped[str] = mapped_column(Text,nullable=False,primary_key=True,)


#用户城市冷区表
class Cold_Zone_Cities(Base,UserIdPKMixin):
    __tablename__ = 'cold_zone_cities'
    city: Mapped[str] = mapped_column(String(50),nullable=False,primary_key=True,)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False,)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,)
    __table_args__ = (CheckConstraint('start_time < end_time',name='cold_zone_cities_check'),)


#用户旅行计划工作台
class Travel_Plan_Workspace(Base,UserIdPKMixin):
    __tablename__ = 'travel_plan_workspace'
    city: Mapped[str] = mapped_column(String(50),primary_key=True, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer,primary_key=True, nullable=False)
    itinerary: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,server_default=text("CURRENT_TIMESTAMP + INTERVAL '7 days'"),)
    __table_args__ = (
        CheckConstraint('end_time > start_time', name='travel_plan_workspace_check'),
        CheckConstraint('version_no > 0', name='travel_plan_workspace_version_no_check'),
    )

#用户城市景点冷区表
class Cold_zone_attractions(Base):
    __tablename__ = 'cold_zone_attractions'
    user_id: Mapped[str] = mapped_column(String(64), nullable=False,primary_key=True)
    city: Mapped[str] = mapped_column(String(50), nullable=False,primary_key=True)
    attraction: Mapped[str] = mapped_column(String(50), nullable=False,primary_key=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint('end_time > start_time', name='cold_zone_attractions_check'),
        ForeignKeyConstraint(['user_id','city'], ['cold_zone_cities.user_id','cold_zone_cities.city'],ondelete='CASCADE', name='cold_zone_attractions_user_id_city_fkey'),
    )

#创建异步Session工厂
SessionLocal = async_sessionmaker(bind=async_engine, class_=AsyncSession,expire_on_commit=False)

async def get_database():
    async with SessionLocal() as session, session.begin():
            yield session

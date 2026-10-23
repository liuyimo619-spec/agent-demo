-- 用户表
CREATE TABLE users(
    user_id VARCHAR(64) PRIMARY KEY,
    create_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    password_hash VARCHAR(255) NOT NULL
);

--令牌+内存记忆活跃表
CREATE TABLE user_sessions(
    user_id VARCHAR(64) NOT NULL,
    token VARCHAR(255) NOT NULL,
    start_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMPTZ NOT NULL DEFAULT(CURRENT_TIMESTAMP + INTERVAL '7 days'),
    last_active TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    PRIMARY KEY (user_id, token),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CHECK (end_time > start_time)
);

-- 用户喜好表
CREATE TABLE user_preferences(
    user_id VARCHAR(64) NOT NULL,
    preference_key VARCHAR(50) NOT NULL,
    preference_value TEXT NOT NULL,

    PRIMARY KEY (user_id, preference_key,preference_value), 
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE   
);
-- 查询用户所有喜好
SELECT * FROM user_preferences WHERE user_id = 'user_id';
-- 查询用户某一类喜好
SELECT * FROM user_preferences WHERE user_id = 'user_id' AND preference_key = 'preference_key';

--用户冷区表
CREATE TABLE cold_zone_cities(
    user_id VARCHAR(64) NOT NULL,
    city VARCHAR(50) NOT NULL,
    start_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMPTZ NOT NULL,

    PRIMARY KEY (user_id, city),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,

    CHECK (end_time > start_time)
);
--触发器函数：自动计算 end_time = start_time + 3年
CREATE OR REPLACE FUNCTION set_cold_zone_end_time()
RETURNS TRIGGER AS $$
BEGIN
    NEW.end_time := NEW.start_time + INTERVAL '3 years';
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
--绑定触发器：INSERT 和 UPDATE start_time 时都触发
CREATE TRIGGER trg_set_cold_zone_end_time
BEFORE INSERT OR UPDATE OF start_time ON cold_zone_cities
FOR EACH ROW
EXECUTE FUNCTION set_cold_zone_end_time();

--用户旅行计划表
CREATE TABLE travel_plans(
    plan_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    city VARCHAR(50) NOT NULL,
    itinerary TEXT NOT NULL,
    summary TEXT,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,

    FOREIGN KEY (user_id)
        REFERENCES users(user_id) 
        ON DELETE CASCADE,
        
    CHECK(
        start_time IS NULL 
        OR end_time IS NULL
        OR end_time >= start_time
    )
);
--创建索引：提高查询效率
CREATE INDEX idx_travel_plans_user_id ON travel_plans(user_id,city);

--用户旅行计划工作台
CREATE TABLE travel_plan_workspace(
    user_id VARCHAR(64) NOT NULL,
    city VARCHAR(50) NOT NULL,
    version_no INTEGER NOT NULL,
    itinerary TEXT NOT NULL,
    summary TEXT,
    start_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMPTZ NOT NULL DEFAULT(CURRENT_TIMESTAMP + INTERVAL '7 days'),

    PRIMARY KEY (user_id, city, version_no),
    FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,
    CHECK (end_time > start_time),
    CHECK (version_no > 0)
);

--用户城市景点冷区表
CREATE TABLE cold_zone_attractions(
    user_id VARCHAR(64) NOT NULL,
    city VARCHAR(50) NOT NULL,
    attraction VARCHAR(50) NOT NULL,
    start_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMPTZ NOT NULL,

    PRIMARY KEY (user_id, city, attraction),
    FOREIGN KEY (user_id,city)
        REFERENCES cold_zone_cities(user_id,city)
        ON DELETE CASCADE,
    CHECK (end_time > start_time)
);
--绑定触发器：INSERT 和 UPDATE start_time 时都触发
CREATE TRIGGER trg_set_cold_zone_attractions_end_time
BEFORE INSERT OR UPDATE OF start_time ON cold_zone_attractions
FOR EACH ROW
EXECUTE FUNCTION set_cold_zone_end_time();
--时区设置
ALTER DATABASE travel_agent_db SET timezone TO 'Asia/Shanghai';

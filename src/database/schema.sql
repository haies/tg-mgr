-- Telegram频道消息元数据表
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    file_unique_id TEXT NOT NULL,
    file_size INTEGER,
    media_type TEXT,
    caption TEXT,  -- 消息文本/媒体说明
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_duplicate BOOLEAN DEFAULT 0,
    is_valid BOOLEAN DEFAULT 1,
    reactions TEXT DEFAULT '{"positive": 0, "heart": 0}',
    source_id INTEGER,  -- 存储转发来源的频道ID
    UNIQUE(message_id)
);

-- 反应数据索引
CREATE INDEX IF NOT EXISTS idx_reactions ON messages(reactions);

-- 文件大小索引（加速过滤查询）
CREATE INDEX IF NOT EXISTS idx_file_size ON messages(file_size);

-- 索引用于加速查询（非唯一）
CREATE INDEX IF NOT EXISTS idx_file_unique_id ON messages(file_unique_id);

-- 消息ID索引（用于快速查找）
CREATE INDEX IF NOT EXISTS idx_message_id ON messages(message_id);

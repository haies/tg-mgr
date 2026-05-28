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
    is_invalid BOOLEAN DEFAULT 0,
    is_junk BOOLEAN DEFAULT 0,  -- 是否垃圾消息
    source_id INTEGER,  -- 存储转发来源的频道ID
    views INTEGER DEFAULT 0,  -- 浏览量
    media_group_id TEXT,  -- 媒体组ID，用于标识同组媒体消息
    channel_id INTEGER,  -- 消息所在频道ID
    media_group_size INTEGER DEFAULT 0,  -- 媒体组全部媒体大小
    reactions INTEGER DEFAULT 0,  -- 反应总数（正向表情累计 + 付费表情*20）
    UNIQUE(message_id, channel_id)
);

-- 反应数据索引
CREATE INDEX IF NOT EXISTS idx_reactions ON messages(reactions);

-- 文件大小索引（加速过滤查询）
CREATE INDEX IF NOT EXISTS idx_file_size ON messages(file_size);

-- 索引用于加速查询（非唯一）
CREATE INDEX IF NOT EXISTS idx_file_unique_id ON messages(file_unique_id);

-- 消息ID索引（用于快速查找）
CREATE INDEX IF NOT EXISTS idx_message_id ON messages(message_id);

-- timestamp 索引（用于排序查询和断点续传）
CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp);

-- channel_id 索引（用于按频道查询）
CREATE INDEX IF NOT EXISTS idx_channel_id ON messages(channel_id);

-- media_group_id 索引（用于媒体组查询）
CREATE INDEX IF NOT EXISTS idx_media_group_id ON messages(media_group_id);
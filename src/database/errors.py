"""数据库错误模块"""


class DatabaseError(Exception):
    """数据库操作基础异常"""
    pass


class DatabaseConnectionError(DatabaseError):
    """数据库连接失败"""
    pass


class DatabaseQueryError(DatabaseError):
    """数据库查询失败"""
    pass


class DatabaseLockError(DatabaseError):
    """数据库锁失败（BEGIN IMMEDIATE 失败）"""
    pass
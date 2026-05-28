"""
Messages 表操作模块单元测试

测试 database/messages.py 中的数据库核心操作
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from database.messages import (
    get_last_processed_id,
    insert_messages,
    get_existing_files,
    check_message_restricted,
)


class TestGetLastProcessedId:
    """测试 get_last_processed_id 函数"""

    def test_get_last_processed_id_with_data(self, populated_db):
        """测试有数据时返回最大 message_id"""
        result = get_last_processed_id(populated_db)
        # sample_messages 中 message_id 最大是 10
        assert result == 10

    def test_get_last_processed_id_empty_db(self, test_db):
        """测试空数据库返回 0"""
        result = get_last_processed_id(test_db)
        assert result == 0

    def test_get_last_processed_id_with_channel_id(self, populated_db):
        """测试按 channel_id 过滤返回该频道最大 message_id

        注意: sample_messages 实际设置的是 source_id 而非 channel_id，
        所以这个测试验证的是 source_id 字段
        """
        cursor = populated_db.cursor()
        # 先设置 channel_id 以便测试
        cursor.execute("UPDATE messages SET channel_id = ? WHERE source_id = ?", (-1001234567890, -1001234567890))
        populated_db.commit()

        result = get_last_processed_id(populated_db, channel_id=-1001234567890)
        assert result == 10

    def test_get_last_processed_id_nonexistent_channel(self, populated_db):
        """测试不存在的 channel_id 返回 0"""
        result = get_last_processed_id(populated_db, channel_id=99999)
        assert result == 0


class TestInsertMessages:
    """测试 insert_messages 函数"""

    def _create_mock_message(
        self,
        msg_id=1,
        file_unique_id="test_file",
        file_size=1024,
        media_type="photo",
        caption="Test caption",
        views=100,
        media_group_id=None,
        reactions=None,
        forward_from_chat=None,
    ):
        """创建模拟的 Message 对象"""
        message = MagicMock()
        message.id = msg_id
        message.caption = caption
        message.text = ""

        # 设置媒体对象
        if media_type == "photo":
            message.photo = MagicMock()
            message.photo.file_unique_id = file_unique_id
            message.photo.file_size = file_size
            # 明确设置 sizes 为 None，避免 MagicMock 自动创建 truthy 空序列
            message.photo.sizes = None
            message.video = None
            message.document = None
            message.animation = None
            message.audio = None
            message.voice = None
            message.video_note = None
            message.text = None
        elif media_type == "video":
            message.video = MagicMock()
            message.video.file_unique_id = file_unique_id
            message.video.file_size = file_size
            message.photo = None
            message.document = None
            message.animation = None
            message.audio = None
            message.voice = None
            message.video_note = None
            message.text = None
        elif media_type == "document":
            message.document = MagicMock()
            message.document.file_unique_id = file_unique_id
            message.document.file_size = file_size
            message.photo = None
            message.video = None
            message.animation = None
            message.audio = None
            message.voice = None
            message.video_note = None
            message.text = None
        else:
            message.photo = None
            message.video = None
            message.document = None
            message.animation = None
            message.audio = None
            message.voice = None
            message.video_note = None
            message.text = media_type

        message.views = views
        message.media_group_id = media_group_id
        message.media = media_type if media_type != "text" else None
        message.forward_from_chat = forward_from_chat
        message.forward_sender_name = None

        # 设置 reactions
        if reactions is not None:
            message.reactions = MagicMock()
            message.reactions.reactions = reactions
        else:
            message.reactions = None

        return message

    def test_insert_messages_new_file(self, test_db):
        """测试插入新文件消息"""
        cursor = test_db.cursor()

        seen_files = set()
        messages = [
            self._create_mock_message(
                msg_id=100,
                file_unique_id="new_file_1",
                file_size=2048,
                media_type="photo",
                caption="New photo",
            )
        ]

        new_files, duplicates, skipped = insert_messages(cursor, messages, seen_files)

        assert len(new_files) == 1
        assert len(duplicates) == 0
        assert skipped == 0
        assert "new_file_1" in seen_files

    def test_insert_messages_duplicate_file(self, test_db):
        """测试重复文件被检测为重复"""
        cursor = test_db.cursor()

        seen_files = {"existing_file"}
        messages = [
            self._create_mock_message(
                msg_id=200,
                file_unique_id="existing_file",
                file_size=1024,
                media_type="photo",
                caption="Duplicate photo",
            )
        ]

        new_files, duplicates, skipped = insert_messages(cursor, messages, seen_files)

        assert len(new_files) == 0
        assert len(duplicates) == 1
        assert skipped == 0
        # duplicates 格式: (message_id, file_unique_id, file_size, media_type, channel_id)
        assert duplicates[0][0] == 200  # message_id
        assert duplicates[0][1] == "existing_file"  # file_unique_id

    def test_insert_messages_skip_empty_file_unique_id(self, test_db):
        """测试跳过空的 file_unique_id"""
        cursor = test_db.cursor()

        seen_files = set()
        messages = [
            self._create_mock_message(
                msg_id=300,
                file_unique_id="",
                file_size=None,
                media_type="text",
                caption="Text only message",
            )
        ]

        new_files, duplicates, skipped = insert_messages(cursor, messages, seen_files)

        assert len(new_files) == 0
        assert len(duplicates) == 0
        assert skipped == 1

    def test_insert_messages_skip_none_file_unique_id(self, test_db):
        """测试跳过 None 的 file_unique_id"""
        cursor = test_db.cursor()

        seen_files = set()
        messages = [
            self._create_mock_message(
                msg_id=301,
                file_unique_id=None,
                file_size=None,
                media_type="text",
                caption="Text only message",
            )
        ]

        new_files, duplicates, skipped = insert_messages(cursor, messages, seen_files)

        assert len(new_files) == 0
        assert len(duplicates) == 0
        assert skipped == 1

    def test_insert_messages_with_media_group_sizes(self, test_db):
        """测试带媒体组大小信息插入"""
        cursor = test_db.cursor()

        seen_files = set()
        messages = [
            self._create_mock_message(
                msg_id=400,
                file_unique_id="grp_file_1",
                file_size=1024,
                media_type="photo",
                caption="Group photo",
                media_group_id="grp123",
            )
        ]

        media_group_sizes = {"grp123": 3}

        new_files, duplicates, skipped = insert_messages(cursor, messages, seen_files, media_group_sizes=media_group_sizes)

        assert len(new_files) == 1
        assert skipped == 0
        # 检查 media_group_size 是否被正确传递
        # new_files 元组格式: (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, source_id, views, media_group_id, channel_id, media_group_size, is_junk)
        assert new_files[0][12] == 3  # media_group_size


class TestGetExistingFiles:
    """测试 get_existing_files 函数"""

    def test_get_existing_files_all_channels(self, populated_db):
        """测试返回所有频道的非重复文件 ID 集合"""
        result = get_existing_files(populated_db)

        assert isinstance(result, set)
        # sample_messages 中 is_duplicate=0 的 file_unique_id: file1(6但6是1的重复所以被标记dup), file2, file3, file4, file5, file6, file7, file8, file9
        # message_id 6 是 is_duplicate=1 的，所以 file1 会重复
        # 实际上 file1 有两条，但第一条(1)不是dup，第二条(6)是dup
        # 所以结果应该只包含第一条的 file_unique_id
        assert "file1" in result  # message_id 1 的 file1
        assert "file2" in result
        assert "file3" in result
        assert "file4" in result
        assert "file5" in result

    def test_get_existing_files_with_channel_id(self, populated_db):
        """测试按 channel_id 过滤返回该频道的文件集合

        注意: sample_messages 实际设置的是 source_id 而非 channel_id，
        所以这个测试需要先设置 channel_id
        """
        cursor = populated_db.cursor()
        # 先设置 channel_id 以便测试
        cursor.execute("UPDATE messages SET channel_id = ? WHERE source_id = ?", (-1001234567890, -1001234567890))
        populated_db.commit()

        result = get_existing_files(populated_db, channel_id=-1001234567890)

        assert isinstance(result, set)
        # -1001234567890 频道有 message_id 9 (file8) 和 10 (file9)
        assert "file8" in result
        assert "file9" in result
        # 其他频道的文件不应该出现
        assert "file1" not in result

    def test_get_existing_files_includes_all_files(self, populated_db):
        """测试返回所有文件，不管 is_duplicate 状态

        is_duplicate=1 表示"该记录被标记为重复"，但文件本身仍然是已见过的。
        去重逻辑应该基于"文件是否在DB中"，而不是"是否被标记为重复"。
        """
        result = get_existing_files(populated_db)

        # file1 有两条记录：message_id 1 (is_duplicate=0) 和 message_id 6 (is_duplicate=1)
        # 但 file1 应该只出现一次（DISTINCT）
        assert "file1" in result

        # 验证 file1 只出现一次
        cursor = populated_db.cursor()
        cursor.execute("SELECT COUNT(DISTINCT message_id) FROM messages WHERE file_unique_id = 'file1'")
        distinct_count = cursor.fetchone()[0]
        assert distinct_count == 2  # 两条记录

        # 验证 result 中 file1 只出现一次（因为是 set）
        file1_count = sum(1 for f in result if f == "file1")
        assert file1_count == 1


class TestCheckMessageRestricted:
    """测试 check_message_restricted 函数"""

    def test_check_restricted_none_message(self):
        """测试 None 消息返回 True"""
        result = check_message_restricted(None)
        assert result is True

    def test_check_restricted_empty_message(self):
        """测试 empty=True 的消息返回 True"""
        mock_message = MagicMock()
        mock_message.empty = True

        result = check_message_restricted(mock_message)
        assert result is True

    def test_check_restricted_with_hard_restrictions(self):
        """测试带有硬性限制原因的消息返回 True"""
        mock_message = MagicMock()
        mock_message.empty = False
        mock_message.restrictions = [
            MagicMock(reason="copyright"),
            MagicMock(reason="violence"),
        ]
        mock_message.forward_from_chat = None
        mock_message.media = None

        result = check_message_restricted(mock_message)
        assert result is True

    def test_check_restricted_with_scam_restriction(self):
        """测试带有 scam 限制原因的消息返回 True"""
        mock_message = MagicMock()
        mock_message.empty = False
        mock_message.restrictions = [MagicMock(reason="scam")]
        mock_message.forward_from_chat = None
        mock_message.media = None

        result = check_message_restricted(mock_message)
        assert result is True

    def test_check_restricted_from_restricted_chat(self):
        """测试转发自有限制频道的消息返回 True"""
        mock_message = MagicMock()
        mock_message.empty = False
        mock_message.restrictions = None
        mock_message.forward_from_chat = MagicMock()
        mock_message.forward_from_chat.restrictions = [
            MagicMock(reason="copyright"),
        ]
        mock_message.media = None

        result = check_message_restricted(mock_message)
        assert result is True

    def test_check_restricted_valid_message(self):
        """测试正常消息返回 False"""
        mock_message = MagicMock()
        mock_message.empty = False
        mock_message.restrictions = None
        mock_message.forward_from_chat = None
        mock_message.media = None

        result = check_message_restricted(mock_message)
        assert result is False

    def test_check_restricted_valid_message_with_media(self):
        """测试带有有效媒体的消息返回 False"""
        mock_message = MagicMock()
        mock_message.empty = False
        mock_message.restrictions = None
        mock_message.forward_from_chat = None
        mock_message.media = "photo"

        # 设置 photo 对象带 file_id
        mock_photo = MagicMock()
        mock_photo.file_id = "valid_file_id_123"
        mock_message.photo = mock_photo
        mock_message.video = None
        mock_message.document = None
        mock_message.animation = None

        result = check_message_restricted(mock_message)
        assert result is False

    def test_check_restricted_media_without_file_id(self):
        """测试媒体存在但 file_id 为 None 返回 True"""
        mock_message = MagicMock()
        mock_message.empty = False
        mock_message.restrictions = None
        mock_message.forward_from_chat = None
        mock_message.media = "video"

        # video 存在但 file_id 为 None
        mock_video = MagicMock()
        mock_video.file_id = None
        mock_message.video = mock_video
        mock_message.photo = None
        mock_message.document = None
        mock_message.animation = None

        result = check_message_restricted(mock_message)
        assert result is True

    def test_check_restricted_with_soft_restrictions(self):
        """测试只有软性限制的消息返回 False"""
        mock_message = MagicMock()
        mock_message.empty = False
        mock_message.restrictions = [
            MagicMock(reason="personal"),  # 不是硬性限制关键词
            MagicMock(reason="other"),
        ]
        mock_message.forward_from_chat = None
        mock_message.media = None

        result = check_message_restricted(mock_message)
        assert result is False
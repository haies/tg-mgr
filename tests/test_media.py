"""
媒体工具模块单元测试

测试 utils/media.py 的媒体信息提取逻辑
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from utils.media import (
    extract_media_info,
    extract_reaction_data,
    extract_source_id,
    row_to_reaction_dict,
    MediaInfo,
    ReactionData,
    STRONG_POSITIVE,
    MILD_POSITIVE,
    HUMOR_POSITIVE,
    PAID_REACTION_MULTIPLIER,
)


class TestExtractMediaInfo:
    """测试 extract_media_info 函数"""

    def test_extract_media_info_photo(self):
        """测试 photo 类型提取"""
        message = MagicMock()
        message.photo = MagicMock()
        message.photo.file_unique_id = "photo_file_123"
        message.photo.file_size = 1024000
        message.photo.sizes = None
        message.views = 100
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "photo"
        assert result.file_unique_id == "photo_file_123"
        assert result.file_size == 1024000
        assert result.views == 100

    def test_extract_media_info_photo_with_sizes(self):
        """测试带 sizes 的 photo 类型提取（选择最大图片）"""
        message = MagicMock()
        message.photo = MagicMock()
        message.photo.sizes = [
            MagicMock(file_unique_id="small", file_size=1000),
            MagicMock(file_unique_id="large", file_size=5000),
            MagicMock(file_unique_id="medium", file_size=3000),
        ]
        message.views = 50
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "photo"
        assert result.file_unique_id == "large"
        assert result.file_size == 5000

    def test_extract_media_info_video(self):
        """测试 video 类型提取"""
        message = MagicMock()
        message.photo = None
        message.video = MagicMock()
        message.video.file_unique_id = "video_file_456"
        message.video.file_size = 2048000
        message.views = 200
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "video"
        assert result.file_unique_id == "video_file_456"
        assert result.file_size == 2048000
        assert result.views == 200

    def test_extract_media_info_document(self):
        """测试 document 类型提取"""
        message = MagicMock()
        message.photo = None
        message.video = None
        message.document = MagicMock()
        message.document.file_unique_id = "doc_file_789"
        message.document.file_size = 512000
        message.views = 75
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "document"
        assert result.file_unique_id == "doc_file_789"
        assert result.file_size == 512000

    def test_extract_media_info_audio(self):
        """测试 audio 类型提取"""
        message = MagicMock()
        message.photo = None
        message.video = None
        message.document = None
        message.audio = MagicMock()
        message.audio.file_unique_id = "audio_file_111"
        message.audio.file_size = 256000
        message.views = 30
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "audio"
        assert result.file_unique_id == "audio_file_111"
        assert result.file_size == 256000

    def test_extract_media_info_animation(self):
        """测试 animation 类型提取"""
        message = MagicMock()
        message.photo = None
        message.video = None
        message.document = None
        message.audio = None
        message.animation = MagicMock()
        message.animation.file_unique_id = "anim_file_222"
        message.animation.file_size = 768000
        message.views = 150
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "animation"
        assert result.file_unique_id == "anim_file_222"
        assert result.file_size == 768000

    def test_extract_media_info_voice(self):
        """测试 voice 类型提取"""
        message = MagicMock()
        message.photo = None
        message.video = None
        message.document = None
        message.audio = None
        message.animation = None
        message.voice = MagicMock()
        message.voice.file_unique_id = "voice_file_333"
        message.voice.file_size = 64000
        message.views = 20
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "voice"
        assert result.file_unique_id == "voice_file_333"
        assert result.file_size == 64000

    def test_extract_media_info_video_note(self):
        """测试 video_note 类型提取"""
        message = MagicMock()
        message.photo = None
        message.video = None
        message.document = None
        message.audio = None
        message.animation = None
        message.voice = None
        message.video_note = MagicMock()
        message.video_note.file_unique_id = "video_note_file_444"
        message.video_note.file_size = 4096000
        message.views = 500
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "video_note"
        assert result.file_unique_id == "video_note_file_444"
        assert result.file_size == 4096000

    def test_extract_media_info_text(self):
        """测试 text 类型提取（无媒体）"""
        message = MagicMock()
        message.text = "Hello world"
        message.photo = None
        message.video = None
        message.document = None
        message.audio = None
        message.animation = None
        message.voice = None
        message.video_note = None
        message.views = 10
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "text"
        assert result.file_unique_id == ""
        assert result.file_size is None

    def test_extract_media_info_other(self):
        """测试 other 类型提取（无识别媒体）"""
        message = MagicMock()
        message.photo = None
        message.video = None
        message.document = None
        message.audio = None
        message.animation = None
        message.voice = None
        message.video_note = None
        message.text = None
        message.views = 5
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.media_type == "other"
        assert result.file_unique_id == ""
        assert result.file_size is None

    def test_extract_media_info_with_views(self):
        """测试 views 提取"""
        message = MagicMock()
        message.text = "Test"
        message.photo = None
        message.video = None
        message.document = None
        message.audio = None
        message.animation = None
        message.voice = None
        message.video_note = None
        message.views = 999
        message.media_group_id = None

        result = extract_media_info(message)

        assert result.views == 999

    def test_extract_media_info_with_media_group_id(self):
        """测试 media_group_id 提取"""
        message = MagicMock()
        message.photo = MagicMock()
        message.photo.file_unique_id = "photo_file"
        message.photo.file_size = 1000
        message.photo.sizes = None
        message.views = 50
        message.media_group_id = "group_abc_123"

        result = extract_media_info(message)

        assert result.media_group_id == "group_abc_123"


class TestExtractReactionData:
    """测试 extract_reaction_data 函数"""

    def test_extract_reaction_data_strong_positive(self):
        """测试 STRONG_POSITIVE 表情（👍）"""
        message = MagicMock()
        reaction = MagicMock()
        reaction.emoji = "👍"
        reaction.count = 5
        reaction.paid = False
        reaction.custom_emoji_id = None
        message.reactions = MagicMock()
        message.reactions.reactions = [reaction]

        result = extract_reaction_data(message)

        assert result.total == 5

    def test_extract_reaction_data_mild_positive(self):
        """测试 MILD_POSITIVE 表情（👌）"""
        message = MagicMock()
        reaction = MagicMock()
        reaction.emoji = "👌"
        reaction.count = 3
        reaction.paid = False
        reaction.custom_emoji_id = None
        message.reactions = MagicMock()
        message.reactions.reactions = [reaction]

        result = extract_reaction_data(message)

        assert result.total == 3

    def test_extract_reaction_data_humor_positive(self):
        """测试 HUMOR_POSITIVE 表情（😂）"""
        message = MagicMock()
        reaction = MagicMock()
        reaction.emoji = "😂"
        reaction.count = 10
        reaction.paid = False
        reaction.custom_emoji_id = None
        message.reactions = MagicMock()
        message.reactions.reactions = [reaction]

        result = extract_reaction_data(message)

        assert result.total == 10

    def test_extract_reaction_data_mixed(self):
        """测试混合多种正向表情"""
        message = MagicMock()
        reactions = [
            MagicMock(emoji="👍", count=5, paid=False, custom_emoji_id=None),
            MagicMock(emoji="👌", count=3, paid=False, custom_emoji_id=None),
            MagicMock(emoji="😂", count=10, paid=False, custom_emoji_id=None),
        ]
        message.reactions = MagicMock()
        message.reactions.reactions = reactions

        result = extract_reaction_data(message)

        assert result.total == 5 + 3 + 10  # 18

    def test_extract_reaction_data_paid_reaction(self):
        """测试付费反应（emoji=None, custom_emoji_id set）"""
        message = MagicMock()
        reaction = MagicMock()
        reaction.emoji = None
        reaction.count = 1
        reaction.paid = False
        reaction.custom_emoji_id = "custom_emoji_123"
        message.reactions = MagicMock()
        message.reactions.reactions = [reaction]

        result = extract_reaction_data(message)

        assert result.total == 1 * PAID_REACTION_MULTIPLIER  # 20

    def test_extract_reaction_data_paid_reaction_with_paid_flag(self):
        """测试付费反应（paid=True flag）"""
        message = MagicMock()
        reaction = MagicMock()
        reaction.emoji = "🎉"
        reaction.count = 2
        reaction.paid = True
        reaction.custom_emoji_id = None
        message.reactions = MagicMock()
        message.reactions.reactions = [reaction]

        result = extract_reaction_data(message)

        assert result.total == 2 * PAID_REACTION_MULTIPLIER  # 40

    def test_extract_reaction_data_no_reactions(self):
        """测试无反应的情况"""
        message = MagicMock()
        message.reactions = None

        result = extract_reaction_data(message)

        assert result.total == 0

    def test_extract_reaction_data_empty_reactions(self):
        """测试空 reactions 列表"""
        message = MagicMock()
        message.reactions = MagicMock()
        message.reactions.reactions = []

        result = extract_reaction_data(message)

        assert result.total == 0

    def test_extract_reaction_data_negative_or_neutral(self):
        """测试负面或中性表情不应被计数"""
        message = MagicMock()
        reactions = [
            MagicMock(emoji="👎", count=100, paid=False, custom_emoji_id=None),  # negative
            MagicMock(emoji="🤔", count=100, paid=False, custom_emoji_id=None),  # neutral
            MagicMock(emoji="😢", count=100, paid=False, custom_emoji_id=None),  # negative
        ]
        message.reactions = MagicMock()
        message.reactions.reactions = reactions

        result = extract_reaction_data(message)

        assert result.total == 0


class TestExtractSourceId:
    """测试 extract_source_id 函数"""

    def test_extract_source_id_forward_from_chat(self):
        """测试从 forward_from_chat 提取源 ID"""
        message = MagicMock()
        message.forward_from_chat = MagicMock()
        message.forward_from_chat.id = -1001234567890
        message.forward_sender_name = None

        result = extract_source_id(message)

        assert result == -1001234567890

    def test_extract_source_id_forward_sender_name(self):
        """测试从 forward_sender_name 提取（返回负的 message id）"""
        message = MagicMock()
        message.forward_from_chat = None
        message.forward_sender_name = "John Doe"
        message.id = 12345

        result = extract_source_id(message)

        assert result == -12345

    def test_extract_source_id_no_forward(self):
        """测试无转发信息时返回 None"""
        message = MagicMock()
        message.forward_from_chat = None
        message.forward_sender_name = None

        result = extract_source_id(message)

        assert result is None


class TestRowToReactionDict:
    """测试 row_to_reaction_dict 函数"""

    def test_row_to_reaction_dict_full_row(self):
        """测试完整行格式：(message_id, total, source_id, views)"""
        row = (1, 50, -1001234567890, 1000)

        result = row_to_reaction_dict(row)

        assert result["message_id"] == 1
        assert result["total"] == 50
        assert result["source_id"] == -1001234567890
        assert result["views"] == 1000

    def test_row_to_reaction_dict_minimal_row(self):
        """测试最小行格式：(message_id, total)"""
        row = (1, 50)

        result = row_to_reaction_dict(row)

        assert result["message_id"] == 1
        assert result["total"] == 50
        assert "source_id" not in result
        assert "views" not in result

    def test_row_to_reaction_dict_with_zero_views(self):
        """测试 views 为 0 时不应被添加"""
        row = (1, 50, None, 0)

        result = row_to_reaction_dict(row)

        assert result["message_id"] == 1
        assert result["total"] == 50
        assert "source_id" not in result
        assert "views" not in result

    def test_row_to_reaction_dict_partial(self):
        """测试部分行格式：(message_id, total, source_id) 但 views=None"""
        row = (1, 50, None, 100)

        result = row_to_reaction_dict(row)

        assert result["message_id"] == 1
        assert result["total"] == 50
        assert "source_id" not in result
        assert result["views"] == 100

    def test_row_to_reaction_dict_null_total(self):
        """测试 total 为 None 时默认为 0"""
        row = (1, None, -1001234567890, 500)

        result = row_to_reaction_dict(row)

        assert result["message_id"] == 1
        assert result["total"] == 0
        assert result["source_id"] == -1001234567890
        assert result["views"] == 500

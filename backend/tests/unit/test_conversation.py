import pytest
from src.ai.conversation import ConversationManager


def test_conversation_manager_turn_flow():
    """Verify appending turns, converse message format, and clear."""
    cm = ConversationManager(max_history_turns=10)

    # Append user turn
    msg1 = cm.append_user_message("Is zone 2 secure?")
    assert msg1["role"] == "user"
    assert msg1["content"] == [{"text": "Is zone 2 secure?"}]
    assert cm.turn_count == 1

    # Append assistant turn
    msg2 = cm.append_assistant_message("Zone 2 is currently unoccupied and secure.")
    assert msg2["role"] == "assistant"
    assert cm.turn_count == 2

    # Check converse messages structure
    msgs = cm.build_converse_messages()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"

    # Test clear
    cm.clear()
    assert cm.turn_count == 0
    assert cm.build_converse_messages() == []


def test_conversation_manager_empty_messages_rejected():
    """Verify empty text messages raise ValueError."""
    cm = ConversationManager()
    with pytest.raises(ValueError):
        cm.append_user_message("   ")

    with pytest.raises(ValueError):
        cm.append_assistant_message("")


def test_conversation_manager_truncation():
    """Verify history truncation to max turns and ensuring starting with user role."""
    cm = ConversationManager(max_history_turns=4)

    cm.append_user_message("User 1")
    cm.append_assistant_message("Bot 1")
    cm.append_user_message("User 2")
    cm.append_assistant_message("Bot 2")
    cm.append_user_message("User 3")
    cm.append_assistant_message("Bot 3")

    # With max_turns=4, messages would be: User 2, Bot 2, User 3, Bot 3
    msgs = cm.build_converse_messages()
    assert len(msgs) == 4
    assert msgs[0]["content"][0]["text"] == "User 2"
    assert msgs[-1]["content"][0]["text"] == "Bot 3"


def test_conversation_manager_token_estimation():
    """Verify local heuristic token estimation."""
    cm = ConversationManager()
    # 20 characters ~ 5 tokens
    cm.append_user_message("12345678901234567890")
    assert cm.estimated_token_count() == 5

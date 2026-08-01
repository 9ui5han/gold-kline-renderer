import json
import uuid


def main(narration_text: str) -> dict:
    try:
        text = str(narration_text or "").strip()
        if not text:
            raise ValueError("旁白定稿的final_full_text为空")

        request_body = {
            "request_id": str(uuid.uuid4()),
            "text": text,
            "tts_provider": "qwen3_tts",
            "qwen3_voice": "Elias",
        }

        return {
            "tts_request_valid": True,
            "tts_request_body_json": json.dumps(
                request_body,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "tts_request_error": "",
            "tts_text_length": len(text),
        }
    except Exception as exc:
        return {
            "tts_request_valid": False,
            "tts_request_body_json": "{}",
            "tts_request_error": str(exc),
            "tts_text_length": 0,
        }

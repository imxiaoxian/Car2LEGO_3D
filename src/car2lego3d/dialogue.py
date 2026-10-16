"""Direct text/vision dialogue using the original project's configured gateway."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path

from dotenv import dotenv_values
import httpx
from PIL import Image

from .common import ROOT, digest, write_json
from .gpt_reference import configuration


def settings():
    source, _ = configuration()
    values = {k: v for layer in (dotenv_values(source / "backend/.env"), dotenv_values(ROOT / ".env"), os.environ)
              for k, v in layer.items() if v is not None}
    key = values.get("OPENAI_API_KEY")
    model = values.get("CAR2LEGO_SPEC_MODEL") or values.get("OPENAI_MODEL")
    if not key or not model:
        raise RuntimeError("请配置 OPENAI_API_KEY 和 OPENAI_MODEL 后获取车型数据。")
    base = (values.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    return key, base if base.endswith("/v1") else base + "/v1", model


def image_content(path):
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((1280, 1280))
        stream = BytesIO()
        image.save(stream, "JPEG", quality=92)
    return {"type": "image_url", "image_url": {
        "url": "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()}}


def decode_json_response(text):
    """Only close outer containers after a complete nested object or array."""
    try:
        return json.loads(text), None
    except json.JSONDecodeError as original:
        if not text.rstrip().endswith(("}", "]")):
            raise original
        stack, quoted, escaped = [], False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == chr(92):
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "{[":
                stack.append("}" if char == "{" else "]")
            elif char in "}]":
                if not stack or stack.pop() != char:
                    raise original
        if quoted or not stack or len(stack) > 8:
            raise original
        suffix = "".join(reversed(stack))
        return json.loads(text + suffix), dict(kind="close_outer_containers", appended=suffix)


def request_json(prompt, schema, directory, images=None, timeout=180):
    """No search tools, agent subprocess, or fabricated web evidence."""
    key, base, model = settings()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    content = [{"type": "text", "text": prompt}]
    for label, path in (images or {}).items():
        content += [{"type": "text", "text": "View: " + label}, image_content(path)]
    messages = [{"role": "system", "content": "Return exactly one valid JSON object. Output JSON only, with no reasoning, commentary or Markdown. Follow this schema: " + json.dumps(schema)},
                {"role": "user", "content": content}]
    # Persist prompts and image hashes, never credentials or base64 image payloads.
    write_json(directory / "request.json", {"model": model, "prompt": prompt, "schema": schema,
               "images": {k: digest(v) for k, v in (images or {}).items()}})
    saved_response = directory / "invalid-response.json"
    if saved_response.is_file():
        saved = json.loads(saved_response.read_text(encoding="utf-8"))
        if (saved.get("request_sha256") == digest(directory / "request.json") and
                saved.get("finish_reason") == "stop"):
            try:
                answer, normalization = decode_json_response(saved["content"])
            except (ValueError, TypeError, KeyError):
                pass
            else:
                import jsonschema
                jsonschema.validate(answer, schema)
                write_json(directory / "answer.json", answer)
                audit = dict(mode="llm_dialogue", model=saved.get("model", model),
                    requested_model=model, response_id=saved.get("response_id"), usage=None,
                    request_sha256=digest(directory / "request.json"),
                    answer_sha256=digest(directory / "answer.json"),
                    raw_response_sha256=digest(saved_response), normalization=normalization,
                    recovered_saved_response=True, new_api_calls=0, web_search_calls=0,
                    physical_measurement_verified=False)
                write_json(directory / "dialogue.json", audit)
                return answer, audit
    try:
        response = httpx.post(base + "/chat/completions", headers={"Authorization": "Bearer " + key},
                              json={"model": model, "messages": messages, "max_tokens": 16000 if images else 6000},
                              timeout=timeout)
    except httpx.HTTPError:
        raise RuntimeError("大模型对话连接失败或超时，请重试。") from None
    if response.status_code != 200:
        raise RuntimeError(f"大模型对话失败，HTTP {response.status_code}。")
    try:
        payload = response.json()
        choice = payload["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError()
    except (KeyError, ValueError, TypeError, IndexError):
        raise ValueError("大模型未返回有效的文本回答，请重试。") from None
    if choice.get("finish_reason") not in (None, "stop"):
        raise ValueError("大模型回答未完整结束，请重试。")
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        answer, normalization = decode_json_response(text)
    except ValueError:
        write_json(directory / "invalid-response.json", {"model": payload.get("model", model),
            "response_id": payload.get("id"), "finish_reason": choice.get("finish_reason"),
            "request_sha256": digest(directory / "request.json"), "content": text})
        raise ValueError("大模型回答不是完整的 JSON，请重试。") from None
    import jsonschema
    jsonschema.validate(answer, schema)
    if normalization:
        write_json(directory / "raw-response.json", dict(model=payload.get("model", model),
            response_id=payload.get("id"), content=text, normalization=normalization))
    write_json(directory / "answer.json", answer)
    audit = {"mode": "llm_dialogue", "model": payload.get("model", model),
             "requested_model": model, "response_id": payload.get("id"), "usage": payload.get("usage"),
             "request_sha256": digest(directory / "request.json"),
             "answer_sha256": digest(directory / "answer.json"), "web_search_calls": 0,
             "physical_measurement_verified": False}
    if normalization:
        audit.update(normalization=normalization, raw_response_sha256=digest(directory / "raw-response.json"))
    write_json(directory / "dialogue.json", audit)
    return answer, audit

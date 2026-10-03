import base64
import io
import json
import time
import uuid
import requests
from PIL import Image
from config import Config


class TokenError(Exception):
    pass


class DuckClient:
    """Unified client for Duck.ai text generation and image editing."""

    HEADERS_BASE = {
        "authority": "duck.ai",
        "accept": "text/event-stream",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://duck.ai",
        "referer": "https://duck.ai/",
        "sec-ch-ua": '"Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"
        ),
        "x-fe-signals": (
            "eyJzdGFydCI6MTc5MTAxMTAzNjUzMCwiZXZlbnRzIjpbeyJuYW1lIjoic3RhcnROZXdDaGF0X2ZyZWUiLCJkZWx0YSI6MTk5fSx7Im5hbWUiOiJhY3Rpb24iLCJkZWx0YSI6MTAyMjYsInRydXN0ZWQiOnRydWV9XSwiZW5kIjoyNjg5M30="
        ),
    }

    def __init__(self, vqd: str = None, harvester_url: str = None):
        self.vqd = vqd
        self.harvester_url = harvester_url or Config.TOKEN_HARVESTER_URL

    # ---------- token ----------
    def fetch_token(self) -> str:
        try:
            r = requests.get(self.harvester_url, timeout=10)
            r.raise_for_status()
            data = r.json()
            if not data.get("success"):
                raise TokenError(f"Harvester returned no token: {data}")
            self.vqd = data["vqd"]
            return self.vqd
        except TokenError:
            raise
        except Exception as e:
            raise TokenError(f"Failed to fetch token: {e}")

    def _headers(self):
        if not self.vqd:
            self.fetch_token()
        h = dict(self.HEADERS_BASE)
        h["x-vqd-hash-1"] = self.vqd
        return h

    # ---------- image prep ----------
    @staticmethod
    def to_data_uri(raw: bytes, max_w: int = 226, max_h: int = 512) -> str:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((max_w, max_h))
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=80)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()

    # ---------- text-only ----------
    def generate_text(self, prompt: str):
        payload = {
            "model": Config.DUCK_MODEL,
            "metadata": {},
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": prompt}]}
            ],
            "canUseTools": True,
            "reasoningEffort": "none",
            "canUseApproxLocation": None,
            "canDelegateImageGeneration": True,
            "canShowGreeting": False,
        }
        return self._stream(payload)

    # ---------- image edit ----------
    def edit_image(self, raw: bytes, prompt: str):
        payload = {
            "model": Config.DUCK_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image",
                            "mimeType": "image/webp",
                            "image": self.to_data_uri(raw),
                        },
                    ],
                }
            ],
            "canUseTools": True,
            "reasoningEffort": "none",
            "canUseApproxLocation": None,
            "canDelegateImageGeneration": None,
            "canShowGreeting": False,
            "durableStream": {
                "messageId": str(uuid.uuid4()),
                "conversationId": str(uuid.uuid4()),
                "publicKey": {
                    "alg": "RSA-OAEP-256",
                    "e": "AQAB",
                    "ext": True,
                    "key_ops": ["encrypt"],
                    "kty": "RSA",
                    "n": (
                        "yNRqjQEU9J18WI0f3NHCUjCJBa095QeXcAaOHe_dPYU2yMLoX__YsOrBhmGiUyPV0nzUl4CI59pXJbiabeJXEGhE9ZvMKtIO7bdRGHRyNXnMx3dEjKkmser-BjbWXr82-kmklXpvwVohCcBpMrHtS32KauSpjCfOaHwkjBhzYdw7VrEmoIqMxaC_VzILMsGeDerYTMpBO5PoG-_rWKGjpzLGlpsvQEFo4LfJ7TvO54qKgsXmIZ6cSNobY5qjNXu-ShK1Yrw2SWjsiRfEx5_yFWdt9aMRjToJC0fqaV7kzZfvduQeBNE4ovUxsDnFg71BvLU7Bpd5dl43xjT89KM-WQ"
                    ),
                    "use": "enc",
                },
            },
        }
        return self._stream(payload)

    # ---------- unified chat ----------
    def chat(self, prompt: str, images: list = None):
        """
        Unified entry point.
        - If images provided -> edit mode.
        - Else -> text/gen mode.
        """
        if images:
            raw = self._decode_data_url(images[0])
            return self.edit_image(raw, prompt)
        return self.generate_text(prompt)

    @staticmethod
    def _decode_data_url(data_url: str) -> bytes:
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        return base64.b64decode(data_url)

    # ---------- streaming core ----------
    def _stream(self, payload: dict):
        headers = self._headers()
        r = requests.post(
            Config.DUCK_CHAT_URL,
            headers=headers,
            json=payload,
            stream=True,
            timeout=120,
        )
        if r.status_code != 200:
            raise TokenError(
                f"duck.ai error {r.status_code}: {r.text[:200]}"
            )

        text_parts = []
        images = []

        for line in r.iter_lines():
            if not line:
                continue
            s = line.decode("utf-8", "ignore")
            if s.startswith("data:"):
                s = s[5:].strip()
            if s == "[DONE]":
                break
            try:
                ev = json.loads(s)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue

            d = ev.get("data")

            # Image (single b64Image)
            if isinstance(d, dict) and d.get("b64Image"):
                try:
                    images.append(base64.b64decode(d["b64Image"]))
                except Exception:
                    pass

            # Images array form
            if isinstance(d, dict) and isinstance(d.get("images"), list):
                for it in d["images"]:
                    if isinstance(it, dict) and it.get("b64"):
                        try:
                            images.append(base64.b64decode(it["b64"]))
                        except Exception:
                            pass

            # Text
            if ev.get("role") == "assistant" and isinstance(ev.get("message"), str):
                text_parts.append(ev["message"])
            if isinstance(d, dict) and isinstance(d.get("text"), str):
                text_parts.append(d["text"])

        return images, "".join(text_parts).strip()

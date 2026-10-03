import base64
import json
import time
import uuid
from functools import wraps

from flask import Flask, request, jsonify, render_template, g
from werkzeug.exceptions import RequestEntityTooLarge

from config import Config
from duck_client import DuckClient, TokenError
from token_pool import pool

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_BODY_BYTES

# Start background token harvester
pool.start()


# ---------------- Helpers ----------------

def api_error(code: str, message: str, status: int = 400):
    return (
        jsonify({"success": False, "error": {"code": code, "message": message}}),
        status,
    )


def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-API-Key")
        if not key:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                key = auth[7:].strip()
        if not key or key not in Config.API_KEYS:
            return api_error("unauthorized", "Missing or invalid API key", 401)
        g.api_key = key
        return f(*args, **kwargs)
    return wrapper


def sniff_format(b: bytes) -> str:
    if b[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return "png"


# ---------------- Routes ----------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/v1/health")
def health():
    return jsonify({"success": True, "data": {"status": "healthy"}})


@app.route("/api/v1/model")
def model():
    return jsonify(
        {"success": True, "data": {"name": Config.DUCK_MODEL, "loaded": True}}
    )


@app.route("/api/v1/pool")
def pool_status():
    return jsonify({"success": True, "data": pool.status()})


@app.route("/api/v1/chat", methods=["POST"])
@require_api_key
def chat():
    t0 = time.time()

    # --- parse body ---
    try:
        body = request.get_json(force=True, silent=False)
    except Exception:
        return api_error("invalid_json", "Request body is not valid JSON", 400)

    if not isinstance(body, dict):
        return api_error("invalid_json", "Request body must be a JSON object", 400)

    prompt = body.get("prompt")
    if not prompt or not isinstance(prompt, str):
        return api_error("validation_error", "Field 'prompt' is required", 422)

    images_in = body.get("images") or []
    if not isinstance(images_in, list):
        return api_error("validation_error", "'images' must be an array", 422)
    if len(images_in) > Config.MAX_IMAGES:
        return api_error(
            "validation_error",
            f"Maximum {Config.MAX_IMAGES} reference images allowed",
            422,
        )

    # decode / size check
    decoded_images = []
    for i, item in enumerate(images_in):
        if not isinstance(item, str) or not item.startswith("data:"):
            return api_error(
                "validation_error",
                f"images[{i}] must be a data URL",
                422,
            )
        try:
            b64 = item.split(",", 1)[1]
            raw = base64.b64decode(b64)
        except Exception:
            return api_error(
                "validation_error",
                f"images[{i}] is not valid base64",
                422,
            )
        if len(raw) > Config.MAX_IMAGE_BYTES:
            return api_error(
                "validation_error",
                f"images[{i}] exceeds 5 MB",
                422,
            )
        decoded_images.append((item, raw))

    max_images = body.get("max_images", 4)
    try:
        max_images = int(max_images)
    except Exception:
        max_images = 4
    max_images = max(0, min(Config.MAX_OUTPUT_IMAGES, max_images))

    # --- call upstream ---
    attempts = 0
    last_err = None
    out_images, out_text = [], ""

    for attempt in range(3):
        attempts += 1
        try:
            vqd = pool.acquire()
            client = DuckClient(vqd=vqd)
            if decoded_images:
                imgs, txt = client.edit_image(decoded_images[0][1], prompt)
            else:
                imgs, txt = client.generate_text(prompt)
            out_images, out_text = imgs, txt
            break
        except TokenError as e:
            last_err = e
            time.sleep(0.5)
            continue
        except Exception as e:
            last_err = e
            break

    if last_err and not out_images and not out_text:
        return api_error("upstream_error", str(last_err), 502)

    # enforce max_images
    if max_images >= 0:
        out_images = out_images[:max_images]

    images_payload = []
    for b in out_images:
        fmt = sniff_format(b)
        images_payload.append(
            {
                "b64": base64.b64encode(b).decode(),
                "format": fmt,
                "size_bytes": len(b),
            }
        )

    latency_ms = round((time.time() - t0) * 1000, 2)

    return jsonify(
        {
            "success": True,
            "data": {
                "model": Config.DUCK_MODEL,
                "text": out_text,
                "images": images_payload,
                "image_count": len(images_payload),
                "attempts": attempts,
                "latency_ms": latency_ms,
            },
        }
    )


# ---------------- Error handlers ----------------

@app.errorhandler(RequestEntityTooLarge)
def too_large(e):
    return api_error(
        "payload_too_large", "Body exceeds 16 MB", 413
    )


@app.errorhandler(404)
def not_found(e):
    return api_error("not_found", "Unknown route", 404)


@app.errorhandler(500)
def server_error(e):
    return api_error("internal_error", "Server-side failure", 500)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bid_assistant.media import ImageGenerationError, generate_image

load_dotenv(PROJECT_ROOT / ".env")


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 OpenAI 兼容图片接口生成一张图片")
    parser.add_argument("--prompt", required=True, help="图片提示词")
    parser.add_argument("--output", required=True, type=Path, help="输出 PNG/JPEG 文件路径")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY", ""))
    parser.add_argument("--model", default=os.getenv("IMAGE_MODEL", "gpt-image-1"))
    parser.add_argument("--size", default="1536x1024", choices=["1024x1024", "1536x1024", "1024x1536"])
    parser.add_argument("--timeout", default=180, type=int)
    args = parser.parse_args()

    try:
        target = generate_image(
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            prompt=args.prompt,
            output_path=args.output,
            size=args.size,
            timeout=args.timeout,
        )
    except ImageGenerationError as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 1
    print(f"已生成：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

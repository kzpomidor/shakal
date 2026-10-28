# meta developer: @you
# requires: Pillow imageio-ffmpeg

import asyncio
import io
import os
import shutil
import tempfile

from PIL import Image
from hikkatl.tl.types import DocumentAttributeAnimated, Message

from .. import loader, utils


@loader.tds
class ShakalMod(loader.Module):
    """Шакалит фото, видео и гифки"""

    strings = {
        "name": "Shakal",
        "no_reply": "<b>🚫 Ответь на фото, видео или гифку</b>",
        "unsupported": "<b>🚫 Этот тип медиа не поддерживается</b>",
        "working": "<b>⏳ Шакалю...</b>",
        "no_ffmpeg": "<b>🚫 ffmpeg не найден. Установи его для видео/гиф</b>",
        "error": "<b>🚫 Ошибка:</b> <code>{}</code>",
        "done": "<b>🗜 Зашакалено (уровень {})</b>",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "default_level",
                5,
                "Уровень шакальности по умолчанию (1-10)",
                validator=loader.validators.Integer(minimum=1, maximum=10),
            ),
        )

    # ---------- photo ----------
    @staticmethod
    def _shakal_image(data: bytes, level: int) -> bytes:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        factor = 1 + level  # 2..11
        small = img.resize(
            (max(1, w // factor), max(1, h // factor)), Image.BILINEAR
        )
        quality = max(1, 30 - level * 3)  # 27..1

        # несколько проходов JPEG для лютого эффекта
        for _ in range(3):
            buf = io.BytesIO()
            small.save(buf, "JPEG", quality=quality)
            buf.seek(0)
            small = Image.open(buf).convert("RGB")

        out = small.resize((w, h), Image.NEAREST)
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=max(1, quality))
        return buf.getvalue()

    # ---------- video / gif ----------
    @staticmethod
    def _ffmpeg_path() -> str:
        path = shutil.which("ffmpeg")
        if path:
            return path
        import imageio_ffmpeg  # встроенный ffmpeg, ставится через pip

        return imageio_ffmpeg.get_ffmpeg_exe()

    async def _shakal_video(self, src: str, dst: str, level: int):
        f = 1 + level  # целый множитель, чтобы размеры оставались чётными
        crf = 30 + level * 2  # 32..50 (макс 51)
        vf = (
            f"scale=trunc(iw/{f}/2)*2:trunc(ih/{f}/2)*2,"
            f"scale=iw*{f}:ih*{f}:flags=neighbor"
        )
        proc = await asyncio.create_subprocess_exec(
            self._ffmpeg_path(), "-y", "-i", src,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "16k", "-ar", "8000",
            "-movflags", "+faststart",
            dst,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(err.decode(errors="ignore")[-300:])

    @loader.command(
        ru_doc="[1-10] <реплай на фото/видео/гиф> — зашакалить медиа",
        en_doc="[1-10] <reply to media> — degrade media quality",
    )
    async def shakalcmd(self, message: Message):
        """[1-10] <reply> — degrade photo/video/gif"""
        reply = await message.get_reply_message()
        if not reply or not reply.media:
            await utils.answer(message, self.strings("no_reply"))
            return

        args = utils.get_args_raw(message).strip()
        level = int(args) if args.isdigit() else self.config["default_level"]
        level = max(1, min(10, level))

        is_gif = bool(reply.gif)
        is_video = bool(reply.video or reply.video_note or is_gif)
        is_photo = bool(reply.photo) or (
            reply.document
            and (reply.document.mime_type or "").startswith("image/")
            and not reply.sticker
        )

        if not (is_photo or is_video):
            await utils.answer(message, self.strings("unsupported"))
            return

        status = await utils.answer(message, self.strings("working"))

        try:
            if is_photo:
                data = await reply.download_media(bytes)
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self._shakal_image, data, level
                )
                file = io.BytesIO(result)
                file.name = "shakal.jpg"
                await message.client.send_file(
                    message.peer_id,
                    file,
                    caption=self.strings("done").format(level),
                    reply_to=reply.id,
                )
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    src = await reply.download_media(os.path.join(tmp, "in"))
                    dst = os.path.join(tmp, "shakal.mp4")
                    try:
                        await self._shakal_video(src, dst, level)
                    except FileNotFoundError:
                        await utils.answer(status, self.strings("no_ffmpeg"))
                        return
                    await message.client.send_file(
                        message.peer_id,
                        dst,
                        caption=self.strings("done").format(level),
                        reply_to=reply.id,
                        supports_streaming=True,
                        attributes=[DocumentAttributeAnimated()] if is_gif else None,
                    )
            await status.delete()
        except Exception as e:
            await utils.answer(status, self.strings("error").format(utils.escape_html(str(e))))
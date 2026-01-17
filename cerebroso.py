import asyncio
import json
import logging
import os
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

import config


logging.basicConfig(level=logging.INFO)


DEFAULT_TIMEZONE = "UTC"


DATA_DIR = os.path.join("data")
DATA_FILE = os.path.join(DATA_DIR, "pomodoro_state.json")


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_timestamp(dt: datetime) -> int:
    return int(dt.timestamp())


def from_timestamp(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def today_key(offset: int = 0, tz: Optional[ZoneInfo] = None) -> str:
    reference = datetime.now(tz or timezone.utc)
    return (reference.date() + timedelta(days=offset)).isoformat()


def parse_hhmm(text: str) -> Optional[Tuple[int, int]]:
    try:
        hour, minute = text.split(":", 1)
        hour_i = int(hour)
        minute_i = int(minute)
    except (ValueError, AttributeError):
        return None
    if 0 <= hour_i < 24 and 0 <= minute_i < 60:
        return hour_i, minute_i
    return None


def parse_datetime_option(text: str, tz: ZoneInfo) -> Optional[int]:
    text = text.strip()
    now = datetime.now(tz)
    if text.startswith("+"):
        number = text[1:-1]
        suffix = text[-1].lower()
        try:
            amount = int(number)
        except ValueError:
            return None
        if suffix == "m":
            delta = timedelta(minutes=amount)
        elif suffix == "h":
            delta = timedelta(hours=amount)
        elif suffix == "d":
            delta = timedelta(days=amount)
        else:
            return None
        return to_timestamp((now + delta).astimezone(timezone.utc))
    hhmm = parse_hhmm(text)
    if hhmm:
        hour, minute = hhmm
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt < now:
            dt += timedelta(days=1)
        return to_timestamp(dt.astimezone(timezone.utc))
    try:
        date_part, time_part = text.split()
        year, month, day = map(int, date_part.split("-"))
        hour, minute = map(int, time_part.split(":"))
        dt = datetime(year, month, day, hour, minute, tzinfo=tz)
        return to_timestamp(dt.astimezone(timezone.utc))
    except Exception:
        return None


def end_of_day_ts(tz: ZoneInfo) -> int:
    now_local = datetime.now(tz)
    next_day = now_local.date() + timedelta(days=1)
    end_local = datetime(next_day.year, next_day.month, next_day.day, tzinfo=tz)
    return to_timestamp(end_local.astimezone(timezone.utc))


def hhmm_list_from_csv(csv: Optional[str]) -> Optional[List[str]]:
    if csv is None:
        return None
    values = []
    for chunk in csv.split(","):
        chunk = chunk.strip()
        hhmm = parse_hhmm(chunk)
        if not hhmm:
            return None
        values.append(f"{hhmm[0]:02d}:{hhmm[1]:02d}")
    return values


def seconds_to_human(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs and not hours:
        parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"


CUTE_MESSAGES = [
    "Você é incrível!",
    "Mandou bem demais!",
    "Orgulho de você!",
    "Continue brilhando!",
    "Que energia boa!",
    "Você arrasou!",
    "Perfeito!",
    "Mais um passo rumo ao sucesso!",
    "Uau! Isso foi ótimo!",
    "Vitória do dia conquistada!",
]


def default_state() -> Dict[str, Any]:
    return {
        "channels": {},
        "reminders": [],
        "habits": [],
        "global_habits": [],
        "_next_ids": {"reminder": 1, "habit": 1, "global_habit": 1},
        "settings": {
            "default_timezone": DEFAULT_TIMEZONE,
            "guild_timezones": {},
            "user_timezones": {},
        },
        "dm_status": {},
        "rotina_summaries": {},
    }


class JsonStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = asyncio.Lock()
        self._data: Dict[str, Any] = default_state()

    async def load(self) -> None:
        ensure_data_dir()
        if not os.path.exists(self.path):
            await self.save()
            return
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, self._read_file)
            if isinstance(data, dict):
                self._data = self._merge_default(data)
        except Exception as exc:
            logging.exception("Falha ao carregar estado JSON: %s", exc)

    def _read_file(self) -> Dict[str, Any]:
        with open(self.path, "r", encoding="utf-8") as fp:
            return json.load(fp)

    def _merge_default(self, loaded: Dict[str, Any]) -> Dict[str, Any]:
        base = default_state()
        for key in base.keys():
            base[key] = loaded.get(key, base[key])
        base.setdefault("_next_ids", {}).update(loaded.get("_next_ids", {}))
        if isinstance(loaded.get("settings"), dict):
            base_settings = base.setdefault("settings", {})
            loaded_settings = loaded.get("settings", {})
            base_settings["default_timezone"] = loaded_settings.get(
                "default_timezone", base_settings.get("default_timezone", DEFAULT_TIMEZONE)
            )
            if isinstance(loaded_settings.get("guild_timezones"), dict):
                base_settings.setdefault("guild_timezones", {}).update(loaded_settings.get("guild_timezones", {}))
            if isinstance(loaded_settings.get("user_timezones"), dict):
                base_settings.setdefault("user_timezones", {}).update(loaded_settings.get("user_timezones", {}))
        return base

    async def save(self) -> None:
        async with self.lock:
            await self._save_locked()

    async def _save_locked(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_file, self._data)

    def _write_file(self, data: Dict[str, Any]) -> None:
        ensure_data_dir()
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.path)

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    async def save_data(self) -> None:
        await self.save()


class PomodoroView(discord.ui.View):
    def __init__(self, bot: "CerebrosoBot", channel_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.channel_id = channel_id

    @discord.ui.button(label="Participar", style=discord.ButtonStyle.success, custom_id="pomodoro_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.bot.add_pomodoro_participant(self.channel_id, interaction.user.id)
        await interaction.response.send_message("Você entrou no ciclo Pomodoro deste canal!", ephemeral=True)

    @discord.ui.button(label="Sair", style=discord.ButtonStyle.danger, custom_id="pomodoro_leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.bot.remove_pomodoro_participant(self.channel_id, interaction.user.id)
        await interaction.response.send_message("Você saiu do ciclo Pomodoro deste canal.", ephemeral=True)


class RotinaButton(discord.ui.View):
    def __init__(self, bot: "CerebrosoBot", rotina_id: int, date_key: str, emoji: str) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.rotina_id = rotina_id
        self.date_key = date_key
        self.emoji = emoji

    @discord.ui.button(label="Fiz!", style=discord.ButtonStyle.primary, custom_id="rotina_fiz")
    async def fiz(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.bot.confirmar_rotina(
            self.rotina_id,
            interaction.user.id,
            self.date_key,
            guild_id=interaction.guild_id,
        )
        await interaction.response.send_message(random.choice(CUTE_MESSAGES), ephemeral=True)


class RotinaDMView(discord.ui.View):
    def __init__(
        self,
        bot: "CerebrosoBot",
        rotina_id: int,
        user_id: int,
        tz: ZoneInfo,
    ) -> None:
        super().__init__(timeout=86400)
        self.bot = bot
        self.rotina_id = rotina_id
        self.user_id = user_id
        self.tz = tz

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Esses botões são só para você 💛", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Não vou fazer hoje", style=discord.ButtonStyle.secondary, custom_id="rotina_skip")
    async def skip_today(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.bot.rotina_skip_today(self.rotina_id, self.user_id, self.tz)
        await interaction.response.send_message(
            "Tudo bem! Vou pausar os lembretes desta rotina até amanhã 🌙",
            ephemeral=True,
        )

    @discord.ui.button(label="Sair da Rotina", style=discord.ButtonStyle.danger, custom_id="rotina_leave_dm")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.bot.rotina_leave(self.rotina_id, self.user_id)
        await interaction.response.send_message(
            "Você saiu dessa rotina. Quando quiser voltar, é só usar `/rotina entrar` 💛",
            ephemeral=True,
        )


class CerebrosoBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.reactions = True
        intents.message_content = False
        super().__init__(command_prefix="!", intents=intents, application_id=None)
        self.store = JsonStore(DATA_FILE)
        self.bg_tasks: List[asyncio.Task[Any]] = []

        self.pomodoro_group = app_commands.Group(name="pomodoro", description="Pomodoro de canal")
        self.lembrete_group = app_commands.Group(name="lembrete", description="Lembretes pessoais por DM")
        self.habito_group = app_commands.Group(name="habito", description="Hábitos pessoais")
        self.rotina_group = app_commands.Group(name="rotina", description="Rotinas da Comunidade")
        self.rotina_admin_group = app_commands.Group(
            name="rotinaadmin",
            description="Administração das Rotinas da Comunidade",
        )
        self.config_group = app_commands.Group(name="config", description="Configurações do Cerebroso")

        self._staff_commands: List[app_commands.Command] = []
        self._general_commands: List[app_commands.Command] = []

        self._register_commands()

    async def setup_hook(self) -> None:
        await self.store.load()
        self.bg_tasks.append(self.loop.create_task(self.reminder_loop()))
        self.bg_tasks.append(self.loop.create_task(self.habito_loop()))
        self.bg_tasks.append(self.loop.create_task(self.rotina_anuncio_loop()))
        self.bg_tasks.append(self.loop.create_task(self.rotina_dm_loop()))
        self.bg_tasks.append(self.loop.create_task(self.rotina_summary_loop()))
        self.bg_tasks.append(self.loop.create_task(self.pomodoro_loop()))

    async def close(self) -> None:
        for task in self.bg_tasks:
            task.cancel()
        await super().close()

    def _settings(self) -> Dict[str, Any]:
        settings = self.store.data.setdefault("settings", {})
        settings.setdefault("default_timezone", DEFAULT_TIMEZONE)
        settings.setdefault("guild_timezones", {})
        settings.setdefault("user_timezones", {})
        return settings

    def set_guild_timezone(self, guild_id: int, tz_name: str) -> None:
        settings = self._settings()
        settings.setdefault("guild_timezones", {})[str(guild_id)] = tz_name

    def clear_guild_timezone(self, guild_id: int) -> None:
        settings = self._settings()
        settings.setdefault("guild_timezones", {}).pop(str(guild_id), None)

    def set_user_timezone(self, user_id: int, tz_name: str) -> None:
        settings = self._settings()
        settings.setdefault("user_timezones", {})[str(user_id)] = tz_name

    def clear_user_timezone(self, user_id: int) -> None:
        settings = self._settings()
        settings.setdefault("user_timezones", {}).pop(str(user_id), None)

    def get_timezone_name(
        self, *, guild_id: Optional[int] = None, user_id: Optional[int] = None
    ) -> str:
        settings = self._settings()
        if user_id is not None:
            tz_name = settings.get("user_timezones", {}).get(str(user_id))
            if tz_name:
                return tz_name
        if guild_id is not None:
            tz_name = settings.get("guild_timezones", {}).get(str(guild_id))
            if tz_name:
                return tz_name
        return settings.get("default_timezone", DEFAULT_TIMEZONE)

    def resolve_timezone(self, *, guild_id: Optional[int] = None, user_id: Optional[int] = None) -> ZoneInfo:
        tz_name = self.get_timezone_name(guild_id=guild_id, user_id=user_id)
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logging.warning("Fuso horário inválido armazenado: %s. Revertendo para UTC.", tz_name)
            return ZoneInfo(DEFAULT_TIMEZONE)

    def _dm_status_entry(self, user_id: int) -> Dict[str, Any]:
        status_map = self.store.data.setdefault("dm_status", {})
        entry = status_map.get(str(user_id))
        if not isinstance(entry, dict):
            entry = {"blocked": False, "next_check": 0, "notified": {}}
        else:
            entry.setdefault("blocked", False)
            entry.setdefault("next_check", 0)
            entry.setdefault("notified", {})
        entry.setdefault("daily_limit", {})
        status_map[str(user_id)] = entry
        return entry

    def _dm_blocked_until(self, user_id: int) -> int:
        entry = self._dm_status_entry(user_id)
        if entry.get("blocked"):
            return int(entry.get("next_check", 0))
        return 0

    async def _mark_dm_success(self, user_id: int) -> None:
        entry = self._dm_status_entry(user_id)
        if entry.get("blocked"):
            entry["blocked"] = False
            entry["next_check"] = 0
            entry["notified"] = {}
            await self.store.save_data()

    async def _handle_dm_blocked(
        self,
        user_id: int,
        channel: Optional[discord.abc.Messageable],
    ) -> None:
        entry = self._dm_status_entry(user_id)
        now_ts = int(time.time())
        entry["blocked"] = True
        entry["next_check"] = now_ts + 300
        notified = entry.setdefault("notified", {})
        limits = entry.setdefault("daily_limit", {})
        if isinstance(channel, discord.TextChannel):
            channel_key = str(channel.id)
            tz = self.resolve_timezone(guild_id=channel.guild.id)
            date_key = today_key(tz=tz)
            info = notified.get(channel_key)
            if isinstance(info, dict):
                last_notified = int(info.get("ts", 0))
                count = int(info.get("count", 0))
                info_date = info.get("date")
            else:
                last_notified = int(info or 0)
                info_date = date_key
                count = 0
            if info_date != date_key:
                count = 0
            if count < 2 and now_ts - last_notified >= 300:
                try:
                    await channel.send(
                        f"⚠️ <@{user_id}>, suas DMs estão fechadas; não consigo te mandar os lembretes das rotinas e hábitos! "
                        "Assim que reabrir, volto a cuidar de você por aqui 💛"
                    )
                    notified[channel_key] = {"ts": now_ts, "count": count + 1, "date": date_key}
                    limits_key = f"{channel.guild.id}:{date_key}"
                    limits[limits_key] = int(limits.get(limits_key, 0)) + 1
                except Exception:
                    logging.exception("Falha ao avisar canal %s sobre DMs fechadas de %s", channel.id, user_id)
        await self.store.save_data()

    async def rotina_skip_today(self, rotina_id: int, user_id: int, tz: ZoneInfo) -> None:
        for rotina in self.store.data.get("global_habits", []):
            if rotina.get("id") != rotina_id:
                continue
            enrollments = rotina.setdefault("enrollments", {})
            prefs = enrollments.get(str(user_id))
            if not isinstance(prefs, dict):
                return
            snooze_until = end_of_day_ts(tz)
            prefs["snooze_until"] = snooze_until
            prefs["next_ts"] = snooze_until
            await self.store.save_data()
            return

    async def rotina_leave(self, rotina_id: int, user_id: int) -> None:
        for rotina in self.store.data.get("global_habits", []):
            if rotina.get("id") != rotina_id:
                continue
            enrollments = rotina.setdefault("enrollments", {})
            if enrollments.pop(str(user_id), None):
                await self.store.save_data()
            return

    def _register_guild_commands(self, guild: discord.abc.Snowflake) -> None:
        for cmd in self._staff_commands:
            self.tree.add_command(cmd, guild=guild)
        for cmd in self._general_commands:
            self.tree.add_command(cmd, guild=guild)
        self.tree.add_command(self.pomodoro_group, guild=guild)
        self.tree.add_command(self.lembrete_group, guild=guild)
        self.tree.add_command(self.habito_group, guild=guild)
        self.tree.add_command(self.rotina_group, guild=guild)
        self.tree.add_command(self.rotina_admin_group, guild=guild)
        self.tree.add_command(self.config_group, guild=guild)

    async def on_ready(self) -> None:
        logging.info("Conectado como %s", self.user)
        for guild in self.guilds:
            try:
                self.tree.clear_commands(guild=guild)
                self._register_guild_commands(guild)
                await self.tree.sync(guild=guild)
            except Exception:
                logging.exception("Falha ao sincronizar comandos em %s", guild.id)

    async def add_pomodoro_participant(self, channel_id: int, user_id: int) -> None:
        channel_data = self.store.data.setdefault("channels", {}).setdefault(str(channel_id), {"config": default_pomodoro_config(), "session": None})
        session = channel_data.get("session") or {}
        participants = set(session.get("participants", []))
        participants.add(user_id)
        session["participants"] = list(participants)
        channel_data["session"] = session
        await self.store.save_data()

    async def remove_pomodoro_participant(self, channel_id: int, user_id: int) -> None:
        channel_data = self.store.data.setdefault("channels", {}).setdefault(str(channel_id), {"config": default_pomodoro_config(), "session": None})
        session = channel_data.get("session") or {}
        participants = set(session.get("participants", []))
        participants.discard(user_id)
        session["participants"] = list(participants)
        channel_data["session"] = session
        await self.store.save_data()

    def _next_id(self, key: str) -> int:
        current = self.store.data.setdefault("_next_ids", {}).get(key, 1)
        self.store.data["_next_ids"][key] = current + 1
        return current

    async def reminder_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                now_ts = int(time.time())
                changed = False
                for reminder in self.store.data.get("reminders", []):
                    if not reminder.get("delivered") and reminder.get("when_ts", 0) <= now_ts:
                        user = self.get_user(reminder["user_id"])
                        if user is None:
                            try:
                                user = await self.fetch_user(reminder["user_id"])
                            except Exception:
                                user = None
                        if user:
                            try:
                                await user.send(f"⏰ **Lembrete:** {reminder['text']}")
                                reminder["delivered"] = True
                                changed = True
                            except Exception:
                                logging.exception("Falha ao enviar DM de lembrete para %s", reminder["user_id"])
                if changed:
                    await self.store.save_data()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Erro no loop de lembretes")
            await asyncio.sleep(30)

    async def habito_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                now_ts = int(time.time())
                changed = False
                for habit in self.store.data.get("habits", []):
                    if not habit.get("active", True):
                        continue
                    channel_id = habit.get("channel_id")
                    channel = self.get_channel(channel_id) if channel_id else None
                    guild_id = habit.get("guild_id")
                    if guild_id is None and isinstance(channel, discord.TextChannel):
                        guild_id = channel.guild.id
                    tz = self.resolve_timezone(guild_id=guild_id, user_id=habit.get("user_id"))
                    goal = max(1, int(habit.get("goal_per_day", 1)))
                    progress = habit.setdefault("progress", {})
                    today = today_key(tz=tz)
                    done_today = progress.get(today, 0)
                    next_ts = habit.get("next_ts", 0)
                    interval_min = max(5, int(habit.get("interval_min", habit.get("interval_min", 60))))
                    if done_today >= goal:
                        continue
                    if next_ts and next_ts > now_ts:
                        continue
                    user_id = habit.get("user_id")
                    if guild_id:
                        guild = self.get_guild(int(guild_id))
                        if guild:
                            member = await self._ensure_member(guild, user_id)
                            if member is None:
                                habit["next_ts"] = now_ts + 86400
                                changed = True
                                continue
                    user = self.get_user(user_id) or await self.fetch_user_safe(user_id)
                    if not user:
                        continue
                    blocked_until = self._dm_blocked_until(user.id)
                    if blocked_until and blocked_until > now_ts:
                        if habit.get("next_ts", 0) < blocked_until:
                            habit["next_ts"] = blocked_until
                            changed = True
                        continue
                    try:
                        emoji = habit.get("emoji", "✅")
                        message = await user.send(
                            f"{emoji} Olá! Hora do hábito **{habit['name']}**. Reaja com {emoji} nesta mensagem para marcar 1x concluído."
                        )
                        try:
                            await message.add_reaction(emoji)
                        except Exception:
                            pass
                        habit["last_message_id"] = message.id
                        habit["last_channel_id"] = message.channel.id
                        habit["next_ts"] = now_ts + interval_min * 60
                        changed = True
                        await self._mark_dm_success(user.id)
                    except discord.Forbidden:
                        await self._handle_dm_blocked(user.id, channel)
                        habit["next_ts"] = now_ts + 300
                        changed = True
                    except Exception:
                        logging.exception("Falha ao enviar lembrete de hábito para %s", habit["user_id"])
                if changed:
                    await self.store.save_data()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Erro no loop de hábitos")
            await asyncio.sleep(30)

    async def rotina_anuncio_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                now_utc = utcnow()
                for rotina in self.store.data.get("global_habits", []):
                    if not rotina.get("active", True):
                        continue
                    channel = self.get_channel(rotina.get("channel_id"))
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    tz = self.resolve_timezone(guild_id=channel.guild.id)
                    today = today_key(tz=tz)
                    now_local = now_utc.astimezone(tz)
                    times = rotina.get("times", ["20:00"])
                    for entry in times:
                        hhmm = parse_hhmm(entry)
                        if not hhmm:
                            continue
                        hour, minute = hhmm
                        if now_local.hour == hour and now_local.minute == minute:
                            ann = rotina.setdefault("announcements", {})
                            daily = ann.get(today)
                            if isinstance(daily, dict) and "message_id" in daily:
                                daily = {"__legacy__": daily}
                                ann[today] = daily
                            if not isinstance(daily, dict):
                                daily = {}
                                ann[today] = daily
                            if entry in daily:
                                continue
                            emoji = rotina.get("emoji", "✅")
                            view = RotinaButton(self, rotina["id"], today, emoji)
                            content = f"{emoji} Rotina **{rotina['name']}**!"
                            role_id = rotina.get("role_id")
                            allowed = discord.AllowedMentions(roles=True, everyone=False, users=False)
                            if role_id:
                                content = f"<@&{role_id}> {content}"
                            try:
                                message = await channel.send(content, view=view, allowed_mentions=allowed)
                                try:
                                    await message.add_reaction(emoji)
                                except Exception:
                                    pass
                                daily[entry] = {
                                    "message_id": message.id,
                                    "ts": to_timestamp(now_utc),
                                    "time": entry,
                                }
                                enrollments = rotina.get("enrollments", {})
                                now_ts = int(time.time())
                                confirmations_map = rotina.setdefault("confirmations", {}).setdefault(today, {})
                                for user_id_str, prefs in enrollments.items():
                                    if not prefs.get("dm", True):
                                        continue
                                    if confirmations_map.get(user_id_str):
                                        continue
                                    snooze_until = int(prefs.get("snooze_until", 0) or 0)
                                    if snooze_until and snooze_until > now_ts:
                                        continue
                                    prefs["next_ts"] = now_ts
                                await self.store.save_data()
                            except Exception:
                                logging.exception("Erro ao anunciar rotina %s", rotina["name"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Erro no loop de anúncios de rotina")
            await asyncio.sleep(30)

    async def rotina_dm_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                now_ts = int(time.time())
                for rotina in self.store.data.get("global_habits", []):
                    if not rotina.get("active", True):
                        continue
                    channel = self.get_channel(rotina.get("channel_id"))
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    guild_id = channel.guild.id
                    tz = self.resolve_timezone(guild_id=guild_id)
                    today = today_key(tz=tz)
                    confirmations = rotina.setdefault("confirmations", {}).setdefault(today, {})
                    enrollments = rotina.get("enrollments", {})
                    emoji = rotina.get("emoji", "✅")
                    announcements = rotina.setdefault("announcements", {})
                    daily = announcements.get(today)
                    ann_info: Optional[Dict[str, Any]] = None
                    if isinstance(daily, dict):
                        if "message_id" in daily:
                            ann_info = daily
                        else:
                            valid_entries = [
                                value
                                for value in daily.values()
                                if isinstance(value, dict) and value.get("message_id")
                            ]
                            if valid_entries:
                                ann_info = max(valid_entries, key=lambda v: int(v.get("ts", 0)))
                    if not isinstance(ann_info, dict):
                        continue
                    message_id = ann_info.get("message_id")
                    if not message_id:
                        continue
                    jump_url = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/{message_id}"
                    save_needed = False
                    for user_id_str, prefs in enrollments.items():
                        user_id = int(user_id_str)
                        if not prefs.get("dm", True):
                            continue
                        if confirmations.get(str(user_id)):
                            continue
                        snooze_until = int(prefs.get("snooze_until", 0) or 0)
                        if snooze_until:
                            if snooze_until > now_ts:
                                continue
                            prefs.pop("snooze_until", None)
                            save_needed = True
                        member = await self._ensure_member(channel.guild, user_id)
                        if member is None:
                            prefs["next_ts"] = now_ts + 86400
                            save_needed = True
                            continue
                        interval_min = max(5, int(prefs.get("interval_min", 90)))
                        next_ts = prefs.get("next_ts", 0)
                        if next_ts and next_ts > now_ts:
                            continue
                        blocked_until = self._dm_blocked_until(user_id)
                        if blocked_until and blocked_until > now_ts:
                            if prefs.get("next_ts", 0) < blocked_until:
                                prefs["next_ts"] = blocked_until
                                save_needed = True
                            continue
                        quiet = prefs.get("quiet", {"start": "06:00", "end": "23:00"})
                        if not self._is_within_window(now_ts, quiet.get("start"), quiet.get("end"), tz):
                            continue
                        user = self.get_user(user_id) or await self.fetch_user_safe(user_id)
                        if not user:
                            continue
                        try:
                            extra = f"\n👉 Confirme aqui: {jump_url}"
                            await user.send(
                                (
                                    f"{emoji} Olá! Já fez a rotina **{rotina['name']}** hoje? "
                                    f"Clique em 'Fiz!' ou reaja com {emoji} no anúncio do servidor.{extra}"
                                ),
                                view=RotinaDMView(self, rotina["id"], user_id, tz),
                            )
                            prefs["next_ts"] = now_ts + interval_min * 60
                            save_needed = True
                            await self._mark_dm_success(user_id)
                        except discord.Forbidden:
                            await self._handle_dm_blocked(user_id, channel)
                            prefs["next_ts"] = now_ts + 300
                            save_needed = True
                        except Exception:
                            logging.exception("Falha ao enviar DM da rotina para %s", user_id)
                    if save_needed:
                        await self.store.save_data()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Erro no loop de DMs de rotina")
            await asyncio.sleep(30)

    async def rotina_summary_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                now_utc = datetime.now(timezone.utc)
                summaries = self.store.data.setdefault("rotina_summaries", {})
                for guild in list(self.guilds):
                    tz = self.resolve_timezone(guild_id=guild.id)
                    now_local = now_utc.astimezone(tz)
                    today = now_local.date().isoformat()
                    if not (now_local.hour == 23 and now_local.minute >= 50):
                        continue
                    guild_state = summaries.setdefault(str(guild.id), {})
                    user_confirmations: Dict[int, List[str]] = defaultdict(list)
                    for rotina in self.store.data.get("global_habits", []):
                        channel = self.get_channel(rotina.get("channel_id"))
                        if not isinstance(channel, discord.TextChannel):
                            continue
                        if channel.guild.id != guild.id:
                            continue
                        confirmations = rotina.get("confirmations", {}).get(today, {})
                        for user_id_str, done in confirmations.items():
                            if not done:
                                continue
                            try:
                                user_id = int(user_id_str)
                            except (TypeError, ValueError):
                                continue
                            user_confirmations[user_id].append(rotina.get("name", "Rotina"))
                    if not user_confirmations:
                        continue
                    save_needed = False
                    for user_id, names in user_confirmations.items():
                        if not names:
                            continue
                        last_sent = guild_state.get(str(user_id))
                        if last_sent == today:
                            continue
                        member = await self._ensure_member(guild, user_id)
                        if member is None:
                            continue
                        try:
                            lines = "\n".join(f"• {name}" for name in names)
                            await member.send(
                                "🌼 Resumo do dia: você marcou as rotinas de hoje!\n" f"{lines}\n\nAté amanhã 💛"
                            )
                            guild_state[str(user_id)] = today
                            save_needed = True
                            await self._mark_dm_success(user_id)
                        except discord.Forbidden:
                            channel = guild.system_channel
                            if not isinstance(channel, discord.TextChannel):
                                channel = None
                            await self._handle_dm_blocked(user_id, channel)
                        except Exception:
                            logging.exception("Falha ao enviar resumo diário para %s", user_id)
                    if save_needed:
                        await self.store.save_data()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Erro no loop de resumos de rotina")
            await asyncio.sleep(60)

    async def fetch_user_safe(self, user_id: int) -> Optional[discord.User]:
        try:
            return await self.fetch_user(user_id)
        except Exception:
            return None

    async def _fetch_member_safe(self, guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
        try:
            return await guild.fetch_member(user_id)
        except Exception:
            return None

    async def _ensure_member(self, guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
        member = guild.get_member(user_id)
        if member:
            return member
        return await self._fetch_member_safe(guild, user_id)

    def _is_within_window(
        self, ts: int, start: Optional[str], end: Optional[str], tz: ZoneInfo
    ) -> bool:
        if not start or not end:
            return True
        hhmm_start = parse_hhmm(start)
        hhmm_end = parse_hhmm(end)
        if not hhmm_start or not hhmm_end:
            return True
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
        minutes_now = dt.hour * 60 + dt.minute
        start_min = hhmm_start[0] * 60 + hhmm_start[1]
        end_min = hhmm_end[0] * 60 + hhmm_end[1]
        if start_min <= end_min:
            return start_min <= minutes_now <= end_min
        return minutes_now >= start_min or minutes_now <= end_min

    def _ensure_rotina_achievements(self, rotina: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        changed = False
        achievements = rotina.get("achievements")
        if not isinstance(achievements, dict):
            achievements = {}
            rotina["achievements"] = achievements
            changed = True
        if "streak_roles" not in achievements or not isinstance(achievements.get("streak_roles"), list):
            achievements["streak_roles"] = []
            changed = True
        monthly = achievements.get("monthly_top")
        if not isinstance(monthly, dict):
            monthly = {"role_id": None, "winner_id": None, "month": None}
            achievements["monthly_top"] = monthly
            changed = True
        else:
            if "role_id" not in monthly:
                monthly["role_id"] = None
                changed = True
            if "winner_id" not in monthly:
                monthly["winner_id"] = None
                changed = True
            if "month" not in monthly:
                monthly["month"] = None
                changed = True
        return achievements, changed

    async def _resolve_rotina_channel(self, rotina: Dict[str, Any]) -> Optional[discord.TextChannel]:
        channel_id = rotina.get("channel_id")
        if not channel_id:
            return None
        channel = self.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        try:
            fetched = await self.fetch_channel(channel_id)
        except Exception:
            return None
        if isinstance(fetched, discord.TextChannel):
            return fetched
        return None

    def _rotina_user_streak(self, rotina: Dict[str, Any], user_id: int) -> int:
        confirmations = rotina.get("confirmations", {})
        channel = self.get_channel(rotina.get("channel_id"))
        guild_id = channel.guild.id if isinstance(channel, discord.TextChannel) else None
        tz = self.resolve_timezone(guild_id=guild_id)
        day = datetime.now(tz).date()
        streak = 0
        while True:
            key = day.isoformat()
            users = confirmations.get(key, {})
            if users.get(str(user_id)):
                streak += 1
                day -= timedelta(days=1)
                continue
            break
        return streak

    def _rotina_monthly_counts(self, rotina: Dict[str, Any], month_key: str) -> List[Tuple[int, int]]:
        confirmations = rotina.get("confirmations", {})
        counts: Dict[int, int] = defaultdict(int)
        for day, users in confirmations.items():
            if not isinstance(day, str) or not day.startswith(month_key):
                continue
            for user_id_str, confirmed in users.items():
                if confirmed:
                    try:
                        uid = int(user_id_str)
                    except (TypeError, ValueError):
                        continue
                    counts[uid] += 1
        return sorted(
            counts.items(),
            key=lambda item: (item[1], self._rotina_user_streak(rotina, item[0]), -item[0]),
            reverse=True,
        )

    async def _remove_role_from_member(self, guild: discord.Guild, role: discord.Role, user_id: int) -> None:
        member = guild.get_member(user_id) or await self._fetch_member_safe(guild, user_id)
        if not member or role not in getattr(member, "roles", []):
            return
        try:
            await member.remove_roles(role, reason="Remoção de conquista da rotina")
        except Exception:
            logging.exception("Falha ao remover cargo de conquista")

    async def _remove_rotina_role(self, rotina: Dict[str, Any], role_id: int, user_id: Optional[int]) -> None:
        if not user_id:
            return
        channel = await self._resolve_rotina_channel(rotina)
        if not channel:
            return
        guild = channel.guild
        role = guild.get_role(role_id)
        if not role:
            return
        await self._remove_role_from_member(guild, role, user_id)

    async def _process_rotina_achievements_and_save(self, rotina: Dict[str, Any], user_id: int) -> None:
        try:
            changed = await self._process_rotina_achievements(rotina, user_id)
        except Exception:
            logging.exception("Falha ao processar conquistas da rotina")
            return
        if changed:
            try:
                await self.store.save_data()
            except Exception:
                logging.exception("Falha ao salvar conquistas da rotina")

    async def _process_rotina_achievements(self, rotina: Dict[str, Any], user_id: int) -> bool:
        achievements, changed = self._ensure_rotina_achievements(rotina)
        channel = await self._resolve_rotina_channel(rotina)
        if not channel:
            return changed
        guild = channel.guild
        member = guild.get_member(user_id) or await self._fetch_member_safe(guild, user_id)
        if not member:
            return changed

        streak_roles = achievements.get("streak_roles", [])
        if isinstance(streak_roles, list) and streak_roles:
            streak = self._rotina_user_streak(rotina, user_id)
            for entry in streak_roles:
                try:
                    role_id = entry.get("role_id")
                    days = int(entry.get("days", 0))
                except Exception:
                    continue
                if not role_id or days <= 0:
                    continue
                if streak >= days:
                    role = guild.get_role(role_id)
                    if role and role not in member.roles:
                        try:
                            await member.add_roles(role, reason="Conquista de streak na rotina")
                        except Exception:
                            logging.exception("Falha ao atribuir cargo de streak")

        monthly = achievements.get("monthly_top")
        if not isinstance(monthly, dict):
            return changed
        role_id = monthly.get("role_id")
        if not role_id:
            return changed
        role = guild.get_role(role_id)
        if not role:
            return changed
        tz = self.resolve_timezone(guild_id=guild.id)
        month_key = datetime.now(tz).strftime("%Y-%m")
        counts = self._rotina_monthly_counts(rotina, month_key)
        previous_month = monthly.get("month")
        previous_winner = monthly.get("winner_id")
        if previous_month and previous_month != month_key and previous_winner:
            await self._remove_role_from_member(guild, role, previous_winner)
            if monthly.get("winner_id") is not None:
                monthly["winner_id"] = None
                changed = True
            previous_winner = None
        if counts:
            top_user, _ = counts[0]
            if previous_winner != top_user:
                if previous_winner:
                    await self._remove_role_from_member(guild, role, previous_winner)
                top_member = guild.get_member(top_user) or await self._fetch_member_safe(guild, top_user)
                if top_member and role not in top_member.roles:
                    try:
                        await top_member.add_roles(role, reason="Top mensal da rotina")
                    except Exception:
                        logging.exception("Falha ao atribuir cargo de top mensal")
                if monthly.get("winner_id") != top_user:
                    monthly["winner_id"] = top_user
                    changed = True
            else:
                winner_member = guild.get_member(previous_winner) or await self._fetch_member_safe(guild, previous_winner)
                if winner_member and role not in winner_member.roles:
                    try:
                        await winner_member.add_roles(role, reason="Top mensal da rotina")
                    except Exception:
                        logging.exception("Falha ao reatribuir cargo de top mensal")
            if monthly.get("month") != month_key:
                monthly["month"] = month_key
                changed = True
        else:
            if previous_winner:
                await self._remove_role_from_member(guild, role, previous_winner)
                if monthly.get("winner_id") is not None:
                    monthly["winner_id"] = None
                    changed = True
            if monthly.get("month") != month_key:
                monthly["month"] = month_key
                changed = True

        return changed

    async def confirmar_rotina(
        self,
        rotina_id: int,
        user_id: int,
        date_key: Optional[str] = None,
        guild_id: Optional[int] = None,
    ) -> None:
        for rotina in self.store.data.get("global_habits", []):
            if rotina.get("id") == rotina_id:
                resolved_date = date_key
                if resolved_date is None:
                    channel = self.get_channel(rotina.get("channel_id"))
                    resolved_guild_id = guild_id or (channel.guild.id if isinstance(channel, discord.TextChannel) else None)
                    tz = self.resolve_timezone(guild_id=resolved_guild_id)
                    resolved_date = today_key(tz=tz)
                confirmations = rotina.setdefault("confirmations", {}).setdefault(resolved_date, {})
                confirmations[str(user_id)] = True
                enroll = rotina.setdefault("enrollments", {})
                prefs = enroll.get(str(user_id))
                if prefs:
                    prefs["next_ts"] = int(time.time()) + max(5, int(prefs.get("interval_min", 90))) * 60
                try:
                    await self._process_rotina_achievements(rotina, user_id)
                except Exception:
                    logging.exception("Falha ao processar conquistas da rotina")
                await self.store.save_data()
                break

    async def pomodoro_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                now_ts = int(time.time())
                changed = False
                for channel_id, channel_data in list(self.store.data.get("channels", {}).items()):
                    session = channel_data.get("session")
                    if not session or not session.get("active"):
                        continue
                    if session.get("paused"):
                        continue
                    last_ts = session.get("last_ts", now_ts)
                    delta = now_ts - last_ts
                    if delta <= 0:
                        continue
                    session["remaining"] = max(0, int(session.get("remaining", 0)) - delta)
                    session["last_ts"] = now_ts
                    if session["remaining"] <= 0:
                        await self._advance_pomodoro(channel_id, channel_data)
                        changed = True
                        continue
                    changed = True
                if changed:
                    await self.store.save_data()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Erro no loop de Pomodoro")
            await asyncio.sleep(5)

    async def _advance_pomodoro(self, channel_id: str, channel_data: Dict[str, Any]) -> None:
        config = channel_data.get("config", default_pomodoro_config())
        session = channel_data.get("session", {})
        channel = self.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        phase = session.get("phase", "foco")
        cycle = session.get("cycle", 0)
        if phase == "foco":
            cycle += 1
            session["cycle"] = cycle
            if cycle % max(1, config.get("cycles_before_long", 4)) == 0:
                session["phase"] = "pausa_longa"
                session["remaining"] = int(config.get("long_break_seconds", 900))
            else:
                session["phase"] = "pausa_curta"
                session["remaining"] = int(config.get("short_break_seconds", 300))
            await channel.send(self._pomodoro_phase_message(session["phase"], session["remaining"]))
        elif phase == "pausa_curta":
            session["phase"] = "foco"
            session["remaining"] = int(config.get("focus_seconds", 1500))
            await channel.send(self._pomodoro_phase_message(session["phase"], session["remaining"]))
        elif phase == "pausa_longa":
            session["phase"] = "foco"
            session["remaining"] = int(config.get("focus_seconds", 1500))
            await channel.send("🎉 Ciclo completo concluído! Preparados para outra rodada de foco?")
            await channel.send(self._pomodoro_phase_message(session["phase"], session["remaining"]))
        session["last_ts"] = int(time.time())
        channel_data["session"] = session

    def _pomodoro_phase_message(self, phase: str, remaining: int) -> str:
        icon = {"foco": "🧠", "pausa_curta": "☕", "pausa_longa": "🛌"}.get(phase, "🧠")
        target_ts = int(time.time()) + remaining
        return f"{icon} Fase: **{phase.replace('_', ' ')}** termina <t:{target_ts}:R> (às <t:{target_ts}:T>)"

    async def send_pomodoro_start(self, channel: discord.TextChannel, channel_data: Dict[str, Any]) -> None:
        config = channel_data.setdefault("config", default_pomodoro_config())
        session = {
            "active": True,
            "phase": "foco",
            "remaining": int(config.get("focus_seconds", 1500)),
            "cycle": 0,
            "participants": [],
            "paused": False,
            "last_ts": int(time.time()),
        }
        channel_data["session"] = session
        view = PomodoroView(self, channel.id)
        message = await channel.send(
            "🧠 Pomodoro iniciado! Clique para participar.", view=view
        )
        session["message_id"] = message.id
        channel_data["session"] = session
        await channel.send(self._pomodoro_phase_message("foco", session["remaining"]))
        await self.store.save_data()

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.user and payload.user_id == self.user.id:
            return
        await self._handle_habit_reaction(payload)
        await self._handle_rotina_reaction(payload)

    async def _handle_habit_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        for habit in self.store.data.get("habits", []):
            if habit.get("last_message_id") == payload.message_id and habit.get("emoji", "✅") == str(payload.emoji):
                last_channel_id = habit.get("last_channel_id")
                if last_channel_id and last_channel_id != payload.channel_id:
                    continue
                if habit.get("user_id") != payload.user_id:
                    continue
                progress = habit.setdefault("progress", {})
                guild_id = habit.get("guild_id") or payload.guild_id
                if guild_id is None:
                    channel = self.get_channel(habit.get("channel_id"))
                    if isinstance(channel, discord.TextChannel):
                        guild_id = channel.guild.id
                tz = self.resolve_timezone(guild_id=guild_id, user_id=payload.user_id)
                today = today_key(tz=tz)
                progress[today] = progress.get(today, 0) + 1
                if progress[today] >= habit.get("goal_per_day", 1):
                    habit["next_ts"] = int(time.time()) + 3600
                await self.store.save_data()
                user = self.get_user(payload.user_id) or await self.fetch_user_safe(payload.user_id)
                if user:
                    try:
                        await user.send(random.choice(CUTE_MESSAGES))
                    except Exception:
                        pass
                break

    async def _handle_rotina_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        tz = self.resolve_timezone(guild_id=payload.guild_id)
        today = today_key(tz=tz)
        for rotina in self.store.data.get("global_habits", []):
            ann = rotina.get("announcements", {}).get(today)
            if not ann:
                continue
            if ann.get("message_id") != payload.message_id:
                continue
            if str(payload.emoji) != rotina.get("emoji", "✅"):
                continue
            await self.confirmar_rotina(rotina["id"], payload.user_id, guild_id=payload.guild_id)

    async def _rotina_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        _ = interaction
        current_lower = current.lower()
        choices = []
        for rotina in self.store.data.get("global_habits", [])[:25]:
            name = rotina.get("name", "Rotina")
            if not current or current_lower in name.lower():
                choices.append(app_commands.Choice(name=f"{rotina['id']} — {name}", value=str(rotina['id'])))
        return choices[:25]

    def _find_rotina(self, identifier: str) -> Optional[Dict[str, Any]]:
        if identifier.isdigit():
            rid = int(identifier)
            for rotina in self.store.data.get("global_habits", []):
                if rotina.get("id") == rid:
                    return rotina
        lowered = identifier.lower()
        for rotina in self.store.data.get("global_habits", []):
            name = rotina.get("name", "").lower()
            if name == lowered:
                return rotina
        for rotina in self.store.data.get("global_habits", []):
            name = rotina.get("name", "").lower()
            if name.startswith(lowered):
                return rotina
        for rotina in self.store.data.get("global_habits", []):
            if lowered in rotina.get("name", "").lower():
                return rotina
        return None

    def _register_commands(self) -> None:
        tree = self.tree

        @tree.command(name="cerebroso", description="Ajuda geral do Cerebroso")
        async def cerebroso_help(interaction: discord.Interaction) -> None:
            embed = discord.Embed(
                title="🧠✨ Cerebroso — seu companheiro de foco e autocuidado!",
                description=(
                    "O Cerebroso é o bot do Ninho que te ajuda a **criar hábitos saudáveis**, "
                    "**lembrar de cuidar de si** e **manter o foco** — tudo de um jeitinho leve e acolhedor 💛\n\n"
                    "Use **`/cerebroso`** pra ver o guia completo direto no Discord."
                ),
                color=discord.Color.blurple(),
            )

            embed.add_field(
                name="🍅 Pomodoro de Canal — modo foco em grupo",
                value=(
                    "Precisa de companhia pra se concentrar?\n"
                    "O comando `/pomodoro iniciar` abre uma sessão de foco no canal.\n"
                    "Você pode **participar**, **pausar** ou **ver o status** da sessão.\n\n"
                    "💡 Trabalhe em blocos de tempo, com pausas entre eles — perfeito pra cérebro TDAH!"
                ),
                inline=False,
            )

            embed.add_field(
                name="⏰ Lembretes pessoais — cuide de si no seu ritmo",
                value=(
                    "Receba lembretes por DM pra não esquecer do básico:\n"
                    "`/lembrete criar texto:'Beber água' quando:'+45m'`\n"
                    "`/lembrete listar` • `/lembrete cancelar id:1`\n\n"
                    "🕒 Use tempos como `+10m`, `+2h` ou `18:00`. Ideal pra lembrar de pausas, remédios ou autocuidado."
                ),
                inline=False,
            )

            embed.add_field(
                name="🌱 Hábitos pessoais — pequenas metas diárias",
                value=(
                    "Acompanhe seus hábitos com carinho!\n"
                    "`/habito criar nome:'Água' meta:8 intervalo_minutos:60 emoji:'💧'`\n"
                    "`/habito listar` • `/habito marcar id:1`\n\n"
                    "💧 Cada vez que marcar, o Cerebroso te manda uma mensagem fofa de incentivo 🩷"
                ),
                inline=False,
            )

            embed.add_field(
                name="🌼 Rotinas da Comunidade — cuidando juntinhos",
                value=(
                    "Rotinas compartilhadas aparecem no canal do dia com um botão **Fiz!** ✨\n"
                    "`/rotina listar` — veja as rotinas disponíveis\n"
                    "`/rotina entrar nome:'Escovar os dentes' intervalo_minutos:90 dm:true`\n"
                    "`/rotina preferencias nome:'Escovar os dentes' janela_inicio:08:00 janela_fim:22:00`\n"
                    "`/rotina leaderboard` — ranking geral\n"
                    "`/rotina leaderboard nome:'Escovar os dentes'` — ranking da rotina específica\n\n"
                    "🎖️ Confirmar pausa seus lembretes até o dia seguinte. Cada check é uma conquista!"
                ),
                inline=False,
            )

            embed.add_field(
                name="📚 Exemplos rápidos",
                value=(
                    "`/pomodoro iniciar`\n"
                    "`/lembrete criar texto:'Alongar' quando:'18:00'`\n"
                    "`/habito criar nome:'Leitura' meta:1 intervalo_minutos:120 emoji:'📚'`\n"
                    "`/rotina entrar nome:'Escovar os dentes' intervalo_minutos:60 dm:true`"
                ),
                inline=False,
            )

            embed.set_footer(
                text="🌿 O Cerebroso não cobra — ele te apoia. Cada passinho já é progresso 💛"
            )

            await interaction.response.send_message(embed=embed)

        self._general_commands = [cerebroso_help]

        config_group = self.config_group

        @config_group.command(name="timezone", description="Define o fuso horário padrão da guilda")
        @app_commands.describe(fuso="Ex.: America/Sao_Paulo", limpar="Voltar ao padrão (UTC)")
        async def config_timezone(
            interaction: discord.Interaction,
            fuso: Optional[str] = None,
            limpar: Optional[bool] = False,
        ) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão para configurar.", ephemeral=True)
                return
            if interaction.guild is None:
                await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
                return
            if limpar:
                self.clear_guild_timezone(interaction.guild.id)
                await self.store.save_data()
                current = self.get_timezone_name(guild_id=interaction.guild.id)
                await interaction.response.send_message(
                    f"Fuso horário restaurado para {current}.", ephemeral=True
                )
                return
            if not fuso:
                current = self.get_timezone_name(guild_id=interaction.guild.id)
                await interaction.response.send_message(
                    f"Fuso horário atual: **{current}**. Informe `fuso` para alterar.", ephemeral=True
                )
                return
            try:
                ZoneInfo(fuso)
            except ZoneInfoNotFoundError:
                await interaction.response.send_message("Fuso horário inválido.", ephemeral=True)
                return
            self.set_guild_timezone(interaction.guild.id, fuso)
            await self.store.save_data()
            await interaction.response.send_message(
                f"Fuso horário da guilda atualizado para **{fuso}**.", ephemeral=True
            )

        @tree.command(name="purgeglobal", description="Limpa comandos globais e re-sincroniza")
        async def purge_global(interaction: discord.Interaction) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Você precisa de permissão de gerenciamento.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                tree.clear_commands(guild=None)
                for cmd in self._staff_commands:
                    tree.add_command(cmd)
                await tree.sync()
                for guild in self.guilds:
                    tree.clear_commands(guild=guild)
                    self._register_guild_commands(guild)
                    await tree.sync(guild=guild)
                await interaction.followup.send("Comandos globais limpos e sincronizados por servidor.", ephemeral=True)
            except Exception:
                logging.exception("Erro no purgeglobal")
                await interaction.followup.send("Erro ao limpar comandos.", ephemeral=True)

        @tree.command(name="syncfix", description="Re-sincroniza comandos desta guild")
        async def syncfix(interaction: discord.Interaction) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Você precisa de permissão de gerenciamento.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                guild = interaction.guild
                if guild is None:
                    await interaction.followup.send("Use em um servidor.", ephemeral=True)
                    return
                self.tree.clear_commands(guild=guild)
                self._register_guild_commands(guild)
                await self.tree.sync(guild=guild)
                await interaction.followup.send("Comandos re-sincronizados com sucesso!", ephemeral=True)
            except Exception:
                logging.exception("Erro no syncfix")
                await interaction.followup.send("Erro ao sincronizar.", ephemeral=True)

        @tree.command(name="debugslash", description="Lista comandos carregados")
        async def debugslash(interaction: discord.Interaction) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Você precisa de permissão de gerenciamento.", ephemeral=True)
                return
            entries = []
            for cmd in self.tree.walk_commands():
                entries.append(cmd.qualified_name)
            await interaction.response.send_message("Comandos registrados:\n" + "\n".join(entries), ephemeral=True)

        self._staff_commands = [purge_global, syncfix, debugslash]

        self._register_pomodoro_commands()
        self._register_lembrete_commands()
        self._register_habito_commands()
        self._register_rotina_commands()

    def _register_pomodoro_commands(self) -> None:
        group = self.pomodoro_group

        @group.command(name="iniciar", description="Inicia o Pomodoro neste canal")
        async def iniciar(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.response.send_message("Execute em um canal de texto.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            channel_id = str(interaction.channel.id)
            channel_data = self.store.data.setdefault("channels", {}).setdefault(channel_id, {"config": default_pomodoro_config(), "session": None})
            await self.send_pomodoro_start(interaction.channel, channel_data)
            await interaction.followup.send("Pomodoro iniciado!", ephemeral=True)

        @group.command(name="status", description="Mostra o status do Pomodoro")
        async def status(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.response.send_message("Execute em um canal de texto.", ephemeral=True)
                return
            channel_id = str(interaction.channel.id)
            channel_data = self.store.data.get("channels", {}).get(channel_id)
            if not channel_data or not channel_data.get("session"):
                await interaction.response.send_message("Nenhum Pomodoro ativo aqui.", ephemeral=True)
                return
            session = channel_data["session"]
            participants = session.get("participants", [])
            embed = discord.Embed(title="Status do Pomodoro", colour=discord.Colour.green())
            embed.add_field(name="Fase", value=session.get("phase", "foco"))
            embed.add_field(name="Tempo restante", value=seconds_to_human(int(session.get("remaining", 0))), inline=False)
            embed.add_field(name="Ciclo", value=str(session.get("cycle", 0)))
            if participants:
                mentions = [f"<@{pid}>" for pid in participants]
                embed.add_field(name="Participantes", value=", ".join(mentions), inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @group.command(name="pausar", description="Pausa o Pomodoro")
        async def pausar(interaction: discord.Interaction) -> None:
            await self._set_pomodoro_pause(interaction, True)

        @group.command(name="retomar", description="Retoma o Pomodoro")
        async def retomar(interaction: discord.Interaction) -> None:
            await self._set_pomodoro_pause(interaction, False)

        @group.command(name="parar", description="Encerra o Pomodoro")
        async def parar(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.response.send_message("Execute em um canal de texto.", ephemeral=True)
                return
            channel_id = str(interaction.channel.id)
            channel_data = self.store.data.get("channels", {}).get(channel_id)
            if not channel_data or not channel_data.get("session"):
                await interaction.response.send_message("Nenhum Pomodoro ativo.", ephemeral=True)
                return
            channel_data["session"] = None
            await self.store.save_data()
            await interaction.response.send_message("Pomodoro encerrado.", ephemeral=True)

        @group.command(name="reiniciar", description="Reinicia o Pomodoro")
        async def reiniciar(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.response.send_message("Execute em um canal de texto.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            channel_id = str(interaction.channel.id)
            channel_data = self.store.data.setdefault("channels", {}).setdefault(channel_id, {"config": default_pomodoro_config(), "session": None})
            channel_data["session"] = None
            await self.send_pomodoro_start(interaction.channel, channel_data)
            await interaction.followup.send("Pomodoro reiniciado!", ephemeral=True)

        @group.command(name="set", description="Ajusta a configuração do Pomodoro")
        @app_commands.describe(foco="Minutos de foco", pausa_curta="Minutos de pausa curta", pausa_longa="Minutos de pausa longa", ciclos="Ciclos antes da pausa longa")
        async def set_config(
            interaction: discord.Interaction,
            foco: int,
            pausa_curta: int,
            pausa_longa: int,
            ciclos: int,
        ) -> None:
            if foco < 5 or pausa_curta < 1 or pausa_longa < 1 or ciclos < 1:
                await interaction.response.send_message("Valores inválidos.", ephemeral=True)
                return
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.response.send_message("Execute em um canal de texto.", ephemeral=True)
                return
            channel_id = str(interaction.channel.id)
            channel_data = self.store.data.setdefault("channels", {}).setdefault(channel_id, {"config": default_pomodoro_config(), "session": None})
            channel_data["config"] = {
                "focus_seconds": foco * 60,
                "short_break_seconds": pausa_curta * 60,
                "long_break_seconds": pausa_longa * 60,
                "cycles_before_long": ciclos,
            }
            await self.store.save_data()
            await interaction.response.send_message("Configuração atualizada!", ephemeral=True)

    async def _set_pomodoro_pause(self, interaction: discord.Interaction, paused: bool) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Execute em um canal de texto.", ephemeral=True)
            return
        channel_id = str(interaction.channel.id)
        channel_data = self.store.data.get("channels", {}).get(channel_id)
        if not channel_data or not channel_data.get("session"):
            await interaction.response.send_message("Nenhum Pomodoro ativo.", ephemeral=True)
            return
        session = channel_data["session"]
        session["paused"] = paused
        session["last_ts"] = int(time.time())
        await self.store.save_data()
        await interaction.response.send_message("Pomodoro pausado." if paused else "Pomodoro retomado!", ephemeral=True)

    def _register_lembrete_commands(self) -> None:
        group = self.lembrete_group

        @group.command(name="criar", description="Cria um lembrete pessoal")
        @app_commands.describe(texto="Texto do lembrete", quando="Quando enviar")
        async def criar(interaction: discord.Interaction, texto: str, quando: str) -> None:
            tz = self.resolve_timezone(guild_id=interaction.guild_id, user_id=interaction.user.id)
            ts = parse_datetime_option(quando, tz)
            if ts is None:
                await interaction.response.send_message("Formato de data inválido.", ephemeral=True)
                return
            reminder = {
                "id": self._next_id("reminder"),
                "user_id": interaction.user.id,
                "when_ts": ts,
                "text": texto,
                "delivered": False,
                "created_ts": int(time.time()),
            }
            self.store.data.setdefault("reminders", []).append(reminder)
            await self.store.save_data()
            await interaction.response.send_message("Lembrete criado!", ephemeral=True)

        @group.command(name="listar", description="Lista seus lembretes")
        async def listar(interaction: discord.Interaction) -> None:
            reminders = [r for r in self.store.data.get("reminders", []) if r.get("user_id") == interaction.user.id and not r.get("delivered")]
            reminders.sort(key=lambda x: x.get("when_ts", 0))
            lines = []
            for reminder in reminders[:10]:
                lines.append(f"#{reminder['id']}: {reminder['text']} — <t:{reminder['when_ts']}:R>")
            if not lines:
                lines.append("Nenhum lembrete pendente.")
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @group.command(name="cancelar", description="Cancela um lembrete")
        async def cancelar(interaction: discord.Interaction, id: int) -> None:
            reminders = self.store.data.get("reminders", [])
            for reminder in reminders:
                if reminder.get("id") == id and reminder.get("user_id") == interaction.user.id:
                    reminder["delivered"] = True
                    await self.store.save_data()
                    await interaction.response.send_message("Lembrete cancelado.", ephemeral=True)
                    return
            await interaction.response.send_message("Lembrete não encontrado.", ephemeral=True)

        @group.command(name="timezone", description="Define seu fuso horário pessoal")
        @app_commands.describe(fuso="Ex.: America/Sao_Paulo", limpar="Voltar ao padrão do servidor")
        async def timezone_cmd(
            interaction: discord.Interaction,
            fuso: Optional[str] = None,
            limpar: Optional[bool] = False,
        ) -> None:
            if limpar:
                self.clear_user_timezone(interaction.user.id)
                await self.store.save_data()
                resolved = self.get_timezone_name(guild_id=interaction.guild_id, user_id=interaction.user.id)
                await interaction.response.send_message(
                    f"Fuso horário pessoal removido. Usando agora: **{resolved}**.", ephemeral=True
                )
                return
            if not fuso:
                current = self.get_timezone_name(guild_id=interaction.guild_id, user_id=interaction.user.id)
                await interaction.response.send_message(
                    f"Fuso horário atual considerado: **{current}**. Informe `fuso` para alterar.", ephemeral=True
                )
                return
            try:
                ZoneInfo(fuso)
            except ZoneInfoNotFoundError:
                await interaction.response.send_message("Fuso horário inválido.", ephemeral=True)
                return
            self.set_user_timezone(interaction.user.id, fuso)
            await self.store.save_data()
            await interaction.response.send_message(
                f"Fuso horário pessoal atualizado para **{fuso}**.", ephemeral=True
            )

    def _register_habito_commands(self) -> None:
        group = self.habito_group

        @group.command(name="criar", description="Cria um hábito pessoal")
        async def criar(
            interaction: discord.Interaction,
            nome: str,
            meta: int,
            intervalo_minutos: int,
            emoji: Optional[str] = None,
        ) -> None:
            if meta <= 0 or intervalo_minutos < 5:
                await interaction.response.send_message("Valores inválidos.", ephemeral=True)
                return
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.response.send_message("Execute em um canal de texto.", ephemeral=True)
                return
            habit = {
                "id": self._next_id("habit"),
                "user_id": interaction.user.id,
                "channel_id": interaction.channel.id,
                "guild_id": interaction.guild_id,
                "name": nome,
                "goal_per_day": meta,
                "interval_min": intervalo_minutos,
                "emoji": emoji or "✅",
                "active": True,
                "next_ts": int(time.time()),
                "last_message_id": None,
                "progress": {},
            }
            self.store.data.setdefault("habits", []).append(habit)
            await self.store.save_data()
            await interaction.response.send_message("Hábito criado com sucesso!", ephemeral=True)

        @group.command(name="listar", description="Lista seus hábitos")
        async def listar(interaction: discord.Interaction) -> None:
            tz = self.resolve_timezone(guild_id=interaction.guild_id, user_id=interaction.user.id)
            today = today_key(tz=tz)
            lines = []
            for habit in self.store.data.get("habits", []):
                if habit.get("user_id") != interaction.user.id:
                    continue
                progress = habit.get("progress", {}).get(today, 0)
                lines.append(
                    f"#{habit['id']} {habit['emoji']} {habit['name']} — {progress}/{habit['goal_per_day']} hoje — próximo em {seconds_to_human(max(0, habit.get('next_ts', int(time.time())) - int(time.time())))}"
                )
            if not lines:
                lines.append("Nenhum hábito cadastrado.")
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @group.command(name="pausar", description="Pausa um hábito")
        async def pausar(interaction: discord.Interaction, id: int) -> None:
            if self._toggle_habit(interaction.user.id, id, False):
                await self.store.save_data()
                await interaction.response.send_message("Hábito pausado.", ephemeral=True)
            else:
                await interaction.response.send_message("Hábito não encontrado.", ephemeral=True)

        @group.command(name="retomar", description="Retoma um hábito")
        async def retomar(interaction: discord.Interaction, id: int) -> None:
            if self._toggle_habit(interaction.user.id, id, True):
                await self.store.save_data()
                await interaction.response.send_message("Hábito retomado.", ephemeral=True)
            else:
                await interaction.response.send_message("Hábito não encontrado.", ephemeral=True)

        @group.command(name="deletar", description="Remove um hábito")
        async def deletar(interaction: discord.Interaction, id: int) -> None:
            habits = self.store.data.get("habits", [])
            for habit in list(habits):
                if habit.get("id") == id and habit.get("user_id") == interaction.user.id:
                    habits.remove(habit)
                    await self.store.save_data()
                    await interaction.response.send_message("Hábito deletado.", ephemeral=True)
                    return
            await interaction.response.send_message("Hábito não encontrado.", ephemeral=True)

        @group.command(name="meta", description="Atualiza a meta diária")
        async def meta(interaction: discord.Interaction, id: int, nova_meta: int) -> None:
            if nova_meta <= 0:
                await interaction.response.send_message("Meta inválida.", ephemeral=True)
                return
            for habit in self.store.data.get("habits", []):
                if habit.get("id") == id and habit.get("user_id") == interaction.user.id:
                    habit["goal_per_day"] = nova_meta
                    await self.store.save_data()
                    await interaction.response.send_message("Meta atualizada!", ephemeral=True)
                    return
            await interaction.response.send_message("Hábito não encontrado.", ephemeral=True)

        @group.command(name="marcar", description="Marca progresso manualmente")
        async def marcar(interaction: discord.Interaction, id: int, quantidade: Optional[int] = 1) -> None:
            quantidade = quantidade or 1
            if quantidade <= 0:
                await interaction.response.send_message("Quantidade inválida.", ephemeral=True)
                return
            for habit in self.store.data.get("habits", []):
                if habit.get("id") == id and habit.get("user_id") == interaction.user.id:
                    progress = habit.setdefault("progress", {})
                    tz = self.resolve_timezone(guild_id=interaction.guild_id, user_id=interaction.user.id)
                    today = today_key(tz=tz)
                    progress[today] = progress.get(today, 0) + quantidade
                    await self.store.save_data()
                    await interaction.response.send_message("Progresso registrado!", ephemeral=True)
                    return
            await interaction.response.send_message("Hábito não encontrado.", ephemeral=True)

    def _toggle_habit(self, user_id: int, habit_id: int, active: bool) -> bool:
        for habit in self.store.data.get("habits", []):
            if habit.get("id") == habit_id and habit.get("user_id") == user_id:
                habit["active"] = active
                return True
        return False

    def _register_rotina_commands(self) -> None:
        group = self.rotina_group
        admin_group = self.rotina_admin_group

        @admin_group.command(name="criar", description="Cria uma rotina comunitária")
        async def admin_criar(
            interaction: discord.Interaction,
            nome: str,
            canal: discord.TextChannel,
            emoji: Optional[str] = None,
            cargo: Optional[discord.Role] = None,
            horarios: Optional[str] = None,
        ) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            times = hhmm_list_from_csv(horarios) if horarios else ["20:00"]
            if times is None:
                await interaction.response.send_message("Horários inválidos.", ephemeral=True)
                return
            rotina = {
                "id": self._next_id("global_habit"),
                "name": nome,
                "emoji": emoji or "✅",
                "role_id": cargo.id if cargo else None,
                "channel_id": canal.id,
                "times": times,
                "active": True,
                "announcements": {},
                "confirmations": {},
                "enrollments": {},
                "achievements": {
                    "streak_roles": [],
                    "monthly_top": {"role_id": None, "winner_id": None, "month": None},
                },
            }
            self.store.data.setdefault("global_habits", []).append(rotina)
            await self.store.save_data()
            await interaction.response.send_message("Rotina criada!", ephemeral=True)

        rotina_autocomplete = app_commands.autocomplete(nome_ou_id=self._rotina_autocomplete)

        @admin_group.command(name="listar", description="Lista rotinas comunitárias")
        async def admin_listar(interaction: discord.Interaction) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            lines = []
            for rotina in self.store.data.get("global_habits", []):
                status = "Ativa" if rotina.get("active", True) else "Pausada"
                achievements = rotina.get("achievements", {})
                streak_roles = achievements.get("streak_roles", [])
                streak_text = ", ".join(
                    f"{item.get('days')}d→<@&{item.get('role_id')}>"
                    for item in streak_roles
                    if item.get("days") and item.get("role_id")
                )
                monthly_role_id = (
                    achievements.get("monthly_top", {}).get("role_id")
                    if isinstance(achievements.get("monthly_top"), dict)
                    else None
                )
                extra = []
                if streak_text:
                    extra.append(f"streaks: {streak_text}")
                if monthly_role_id:
                    extra.append(f"top mensal: <@&{monthly_role_id}>")
                extra_text = f" — conquistas: {', '.join(extra)}" if extra else ""
                lines.append(
                    f"#{rotina['id']} {rotina['name']} — canal: <#{rotina['channel_id']}> — horários: {', '.join(rotina.get('times', []))} — {status}{extra_text}"
                )
            if not lines:
                lines.append("Nenhuma rotina cadastrada.")
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @admin_group.command(name="pausar", description="Pausa uma rotina")
        @rotina_autocomplete
        async def admin_pausar(interaction: discord.Interaction, nome_ou_id: str) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            rotina["active"] = False
            await self.store.save_data()
            await interaction.response.send_message("Rotina pausada.", ephemeral=True)

        @admin_group.command(name="retomar", description="Retoma uma rotina")
        @rotina_autocomplete
        async def admin_retomar(interaction: discord.Interaction, nome_ou_id: str) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            rotina["active"] = True
            await self.store.save_data()
            await interaction.response.send_message("Rotina retomada.", ephemeral=True)

        @admin_group.command(name="deletar", description="Remove uma rotina")
        @rotina_autocomplete
        async def admin_deletar(interaction: discord.Interaction, nome_ou_id: str) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            self.store.data.get("global_habits", []).remove(rotina)
            await self.store.save_data()
            await interaction.response.send_message("Rotina deletada.", ephemeral=True)

        @admin_group.command(name="editar", description="Edita uma rotina")
        @rotina_autocomplete
        async def admin_editar(
            interaction: discord.Interaction,
            nome_ou_id: str,
            nome: Optional[str] = None,
            emoji: Optional[str] = None,
            cargo: Optional[discord.Role] = None,
            canal: Optional[discord.TextChannel] = None,
            horarios: Optional[str] = None,
        ) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            if nome:
                rotina["name"] = nome
            if emoji:
                rotina["emoji"] = emoji
            if cargo is not None:
                rotina["role_id"] = cargo.id
            if canal is not None:
                rotina["channel_id"] = canal.id
            if horarios is not None:
                times = hhmm_list_from_csv(horarios)
                if times is None:
                    await interaction.response.send_message("Horários inválidos.", ephemeral=True)
                    return
                rotina["times"] = times
            await self.store.save_data()
            await interaction.response.send_message("Rotina atualizada.", ephemeral=True)

        @admin_group.command(name="conquista_streak", description="Configura cargo por streak")
        @rotina_autocomplete
        async def admin_conquista_streak(
            interaction: discord.Interaction,
            nome_ou_id: str,
            dias: int,
            cargo: discord.Role,
        ) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            if dias <= 0:
                await interaction.response.send_message("Informe um número de dias válido.", ephemeral=True)
                return
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            achievements, _ = self._ensure_rotina_achievements(rotina)
            streak_roles = achievements.setdefault("streak_roles", [])
            updated = False
            for item in streak_roles:
                if int(item.get("days", 0)) == dias:
                    item["role_id"] = cargo.id
                    updated = True
                    break
            if not updated:
                streak_roles.append({"days": dias, "role_id": cargo.id})
            streak_roles.sort(key=lambda entry: int(entry.get("days", 0)))
            await self.store.save_data()
            await interaction.response.send_message(
                f"Cargo configurado para streak de {dias} dias.", ephemeral=True
            )
            self.loop.create_task(self._process_rotina_achievements_and_save(rotina, interaction.user.id))

        @admin_group.command(name="conquista_streak_remover", description="Remove cargo de streak")
        @rotina_autocomplete
        async def admin_conquista_streak_remover(
            interaction: discord.Interaction,
            nome_ou_id: str,
            dias: int,
        ) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            achievements, _ = self._ensure_rotina_achievements(rotina)
            streak_roles = achievements.setdefault("streak_roles", [])
            before = len(streak_roles)
            streak_roles[:] = [item for item in streak_roles if int(item.get("days", 0)) != dias]
            if len(streak_roles) == before:
                await interaction.response.send_message("Nenhum cargo configurado para esse streak.", ephemeral=True)
                return
            await self.store.save_data()
            await interaction.response.send_message("Cargo removido das conquistas de streak.", ephemeral=True)

        @admin_group.command(name="conquista_topmensal", description="Configura cargo para o top mensal")
        @rotina_autocomplete
        async def admin_conquista_topmensal(
            interaction: discord.Interaction,
            nome_ou_id: str,
            cargo: discord.Role,
        ) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            achievements, _ = self._ensure_rotina_achievements(rotina)
            monthly = achievements.setdefault("monthly_top", {"role_id": None, "winner_id": None, "month": None})
            monthly["role_id"] = cargo.id
            monthly["winner_id"] = None
            monthly["month"] = None
            await self.store.save_data()
            await interaction.response.send_message("Cargo configurado para o top mensal.", ephemeral=True)
            self.loop.create_task(self._process_rotina_achievements_and_save(rotina, interaction.user.id))

        @admin_group.command(name="conquista_topmensal_remover", description="Remove o cargo de top mensal")
        @rotina_autocomplete
        async def admin_conquista_topmensal_remover(
            interaction: discord.Interaction,
            nome_ou_id: str,
        ) -> None:
            if not has_manage_permission(interaction.user):
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            achievements, _ = self._ensure_rotina_achievements(rotina)
            monthly = achievements.setdefault("monthly_top", {"role_id": None, "winner_id": None, "month": None})
            role_id = monthly.get("role_id")
            winner_id = monthly.get("winner_id")
            monthly["role_id"] = None
            monthly["winner_id"] = None
            monthly["month"] = None
            await self.store.save_data()
            await interaction.response.send_message("Cargo de top mensal removido.", ephemeral=True)
            if role_id:
                self.loop.create_task(self._remove_rotina_role(rotina, role_id, winner_id))

        @group.command(name="listar", description="Mostra as rotinas disponíveis")
        async def listar(interaction: discord.Interaction) -> None:
            embed = discord.Embed(
                title="Rotinas disponíveis",
                colour=discord.Colour.green(),
            )
            tz = self.resolve_timezone(guild_id=interaction.guild_id)
            tz_label = getattr(tz, "key", None) or tz.tzname(datetime.now(tz)) or "UTC"
            count = 0
            for rotina in self.store.data.get("global_habits", []):
                if not rotina.get("active", True):
                    continue
                channel_id = rotina.get("channel_id")
                channel_mention = "Canal não definido"
                channel = None
                if channel_id:
                    channel = self.get_channel(channel_id)
                    if channel is None and interaction.guild:
                        channel = interaction.guild.get_channel(channel_id)
                    channel_guild = getattr(channel, "guild", None)
                    if (
                        channel_guild
                        and interaction.guild
                        and channel_guild.id != interaction.guild.id
                    ):
                        continue
                    channel_mention = channel.mention if channel else f"<#{channel_id}>"
                times_list = rotina.get("times", [])
                times_text = ", ".join(times_list) if times_list else "Horários não definidos"
                emoji = rotina.get("emoji") or "✅"
                name = rotina.get("name", "Rotina")
                embed.add_field(
                    name=f"{emoji} {discord.utils.escape_markdown(name)}",
                    value=(
                        f"Canal: {channel_mention}\n"
                        f"Horários: {times_text}\n"
                        "Use `/rotina entrar` e escolha pelo nome para participar."
                    ),
                    inline=False,
                )
                count += 1
            if count == 0:
                embed.description = "Nenhuma rotina ativa disponível no momento."
            else:
                embed.set_footer(text=f"Horários exibidos em {tz_label}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @group.command(name="entrar", description="Participa de uma rotina")
        @rotina_autocomplete
        async def entrar(
            interaction: discord.Interaction,
            nome_ou_id: str,
            intervalo_minutos: Optional[int] = 90,
            dm: Optional[bool] = True,
        ) -> None:
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            enroll = rotina.setdefault("enrollments", {})
            prefs = enroll.setdefault(str(interaction.user.id), {"quiet": {"start": "06:00", "end": "23:00"}})
            prefs["dm"] = dm if dm is not None else prefs.get("dm", True)
            prefs["interval_min"] = max(5, intervalo_minutos or prefs.get("interval_min", 90))
            prefs.setdefault("quiet", {"start": "06:00", "end": "23:00"})
            prefs["next_ts"] = int(time.time())
            await self.store.save_data()
            await interaction.response.send_message("Inscrição registrada!", ephemeral=True)

        @group.command(name="sair", description="Remove sua participação")
        @rotina_autocomplete
        async def sair(interaction: discord.Interaction, nome_ou_id: str) -> None:
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            enroll = rotina.setdefault("enrollments", {})
            if enroll.pop(str(interaction.user.id), None):
                await self.store.save_data()
                await interaction.response.send_message("Você saiu da rotina.", ephemeral=True)
            else:
                await interaction.response.send_message("Você não estava inscrito.", ephemeral=True)

        @group.command(name="preferencias", description="Atualiza preferências da rotina")
        @rotina_autocomplete
        async def preferencias(
            interaction: discord.Interaction,
            nome_ou_id: str,
            intervalo_minutos: Optional[int] = None,
            dm: Optional[bool] = None,
            janela_inicio: Optional[str] = None,
            janela_fim: Optional[str] = None,
        ) -> None:
            rotina = self._find_rotina(nome_ou_id)
            if not rotina:
                await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                return
            enroll = rotina.setdefault("enrollments", {})
            prefs = enroll.setdefault(str(interaction.user.id), {"quiet": {"start": "06:00", "end": "23:00"}})
            if intervalo_minutos is not None:
                if intervalo_minutos < 5:
                    await interaction.response.send_message("Intervalo mínimo é 5 minutos.", ephemeral=True)
                    return
                prefs["interval_min"] = intervalo_minutos
            if dm is not None:
                prefs["dm"] = dm
            quiet = prefs.setdefault("quiet", {"start": "06:00", "end": "23:00"})
            if janela_inicio:
                if not parse_hhmm(janela_inicio):
                    await interaction.response.send_message("Horário inválido.", ephemeral=True)
                    return
                quiet["start"] = janela_inicio
            if janela_fim:
                if not parse_hhmm(janela_fim):
                    await interaction.response.send_message("Horário inválido.", ephemeral=True)
                    return
                quiet["end"] = janela_fim
            await self.store.save_data()
            await interaction.response.send_message("Preferências atualizadas!", ephemeral=True)

        @group.command(name="meus", description="Lista suas inscrições")
        async def meus(interaction: discord.Interaction) -> None:
            lines = []
            for rotina in self.store.data.get("global_habits", []):
                prefs = rotina.get("enrollments", {}).get(str(interaction.user.id))
                if not prefs:
                    continue
                lines.append(
                    f"{rotina['name']} — DM: {'sim' if prefs.get('dm', True) else 'não'} — intervalo: {prefs.get('interval_min', 90)} min — janela: {prefs.get('quiet', {}).get('start', '??')}–{prefs.get('quiet', {}).get('end', '??')}"
                )
            if not lines:
                lines.append("Você não está inscrito em nenhuma rotina.")
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @group.command(name="leaderboard", description="Leaderboard de uma rotina")
        async def leaderboard(interaction: discord.Interaction, nome: Optional[str] = None) -> None:
            if nome:
                rotina = self._find_rotina(nome)
                if not rotina:
                    await interaction.response.send_message("Rotina não encontrada.", ephemeral=True)
                    return
                embed = self._build_rotina_leaderboard(rotina)
                await interaction.response.send_message(embed=embed)
            else:
                embed = self._build_global_leaderboard(interaction.guild_id)
                await interaction.response.send_message(embed=embed)

        @group.command(name="leaderboardgeral", description="Leaderboard geral das rotinas")
        async def leaderboardgeral(interaction: discord.Interaction) -> None:
            embed = self._build_global_leaderboard(interaction.guild_id)
            await interaction.response.send_message(embed=embed)

    def _build_rotina_leaderboard(self, rotina: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(title=f"Leaderboard — {rotina['name']}", colour=discord.Colour.gold())
        stats = self._rotina_stats(rotina)
        if not stats:
            embed.description = "Ainda não há confirmações."
            return embed
        lines = []
        for idx, (user_id, info) in enumerate(stats[:10], start=1):
            lines.append(
                f"#{idx} <@{user_id}> — streak: {info['streak']} dias — 30d: {info['total']}"
            )
        embed.description = "\n".join(lines)
        return embed

    def _rotina_stats(self, rotina: Dict[str, Any]) -> List[Tuple[int, Dict[str, int]]]:
        confirmations = rotina.get("confirmations", {})
        cutoff = utcnow().date() - timedelta(days=29)
        per_user: Dict[int, Dict[str, int]] = defaultdict(lambda: {"total": 0, "streak": 0, "last_day": None})
        for day in sorted(confirmations.keys()):
            day_date = datetime.fromisoformat(day).date()
            if day_date < cutoff:
                continue
            users = confirmations.get(day, {})
            for user_id_str, confirmed in users.items():
                if not confirmed:
                    continue
                user_id = int(user_id_str)
                info = per_user[user_id]
                info["total"] += 1
                last_day = info.get("last_day")
                if last_day is None or day_date - last_day > timedelta(days=1):
                    info["streak"] = 1
                elif day_date - last_day == timedelta(days=1):
                    info["streak"] += 1
                else:
                    info["streak"] = max(info["streak"], 1)
                info["last_day"] = day_date
        for info in per_user.values():
            info.pop("last_day", None)
        sorted_users = sorted(
            per_user.items(),
            key=lambda item: (item[1].get("streak", 0), item[1].get("total", 0)),
            reverse=True,
        )
        return sorted_users

    def _build_global_leaderboard(self, guild_id: Optional[int]) -> discord.Embed:
        embed = discord.Embed(title="Leaderboard Geral — Rotinas", colour=discord.Colour.blue())
        per_user: Dict[int, Dict[str, int]] = defaultdict(lambda: {"total": 0, "streak": 0})
        for rotina in self.store.data.get("global_habits", []):
            channel_id = rotina.get("channel_id")
            if guild_id and channel_id:
                channel = self.get_channel(channel_id)
                if channel and isinstance(channel, discord.abc.GuildChannel) and channel.guild.id != guild_id:
                    continue
            stats = self._rotina_stats(rotina)
            for user_id, info in stats:
                agg = per_user[user_id]
                agg["total"] += info["total"]
                agg["streak"] += info["streak"]
        if not per_user:
            embed.description = "Sem dados suficientes ainda."
            return embed
        top = sorted(per_user.items(), key=lambda i: (i[1]["total"], i[1]["streak"]), reverse=True)[:10]
        lines = []
        for idx, (user_id, info) in enumerate(top, start=1):
            lines.append(f"#{idx} <@{user_id}> — 30d: {info['total']} — streaks somados: {info['streak']}")
        embed.description = "\n".join(lines)
        if top:
            top_user = top[0][0]
            destaque = []
            for rotina in self.store.data.get("global_habits", []):
                stats_dict = dict(self._rotina_stats(rotina))
                info = stats_dict.get(top_user)
                if info:
                    destaque.append(f"{rotina['name']}: {info['total']}")
                if len(destaque) >= 4:
                    break
            if destaque:
                embed.add_field(name="Destaques do #1", value="\n".join(destaque), inline=False)
        return embed


def has_manage_permission(user: discord.abc.User) -> bool:
    if isinstance(user, discord.Member):
        perms = user.guild_permissions
        return perms.manage_guild or perms.manage_roles or perms.administrator
    return False


def default_pomodoro_config() -> Dict[str, int]:
    return {
        "focus_seconds": 1500,
        "short_break_seconds": 300,
        "long_break_seconds": 900,
        "cycles_before_long": 4,
    }


bot = CerebrosoBot()
bot.tree.add_command(bot.pomodoro_group)
bot.tree.add_command(bot.lembrete_group)
bot.tree.add_command(bot.habito_group)
bot.tree.add_command(bot.rotina_group)


if __name__ == "__main__":
    bot.run(config.DISCORD_TOKEN)

# -*- coding: utf-8 -*-
import os
import json
import uuid
from typing import Optional, List, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
from aiohttp import web
import threading
import discord
from discord.ext import commands

import sys, types
sys.modules["discord.voice_client"] = types.ModuleType("discord.voice_client")

# ──────────────────────────────────────────────────────────────────────────────
# Setup & Intents
# ──────────────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_USER_ID = 1211683189186105434  # Deine User-ID (zusätzlich zu Server-Admins)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ──────────────────────────────────────────────────────────────────────────────
# Render Hosting
# ──────────────────────────────────────────────────────────────────────────────

async def handle(request):
    return web.Response(text="Bot läuft!")

def run_web():
    app = web.Application()
    app.router.add_get("/", handle)
    web.run_app(app, host="0.0.0.0", port=8080)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.start()

# ──────────────────────────────────────────────────────────────────────────────
# Datenhaltung
# ──────────────────────────────────────────────────────────────────────────────
def data_file(gid: int) -> str:
    os.makedirs("data", exist_ok=True)
    return f"data/{gid}.json"

def load_data(gid: int) -> dict:
    p = data_file(gid)
    if not os.path.exists(p):
        return {
            "config": {
                "log_channel": None,
                "dn_channel": None,
                "roles": [],        # Berechtigte
                "mitarbeiter": []   # Mitarbeiter
            },
            "dienstnummern": {},     # {str(user_id): "DN"}
            "reserved": [],          # ["DN"] – reservierte (blockierte) Nummern bis Entscheidung
            "logs": {}               # {log_id: {...}}
        }
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(gid: int, d: dict):
    with open(data_file(gid), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=4, ensure_ascii=False)

# ──────────────────────────────────────────────────────────────────────────────
# Zeit & Hilfsfunktionen
# ──────────────────────────────────────────────────────────────────────────────
def now_dt():
    return datetime.now(ZoneInfo("Europe/Berlin"))

def now_str():
    return now_dt().strftime("%d.%m.%Y %H:%M:%S")

def is_admin(inter: discord.Interaction) -> bool:
    return inter.user.id == ADMIN_USER_ID or inter.user.guild_permissions.administrator

def is_berechtigt(inter: discord.Interaction) -> bool:
    d = load_data(inter.guild.id)
    return is_admin(inter) or any(r.id in d["config"].get("roles", []) for r in inter.user.roles)

def is_mitarbeiter(inter: discord.Interaction) -> bool:
    d = load_data(inter.guild.id)
    return any(r.id in d["config"].get("mitarbeiter", []) for r in inter.user.roles)

def number_in_use(d: dict, nummer: str) -> bool:
    return nummer in set(d.get("dienstnummern", {}).values())

def number_reserved(d: dict, nummer: str) -> bool:
    return nummer in set(d.get("reserved", []))

def reserve_number(d: dict, nummer: str):
    s = set(d.get("reserved", []))
    s.add(nummer)
    d["reserved"] = list(s)

def release_number(d: dict, nummer: Optional[str]):
    if not nummer:
        return
    s = set(d.get("reserved", []))
    if nummer in s:
        s.remove(nummer)
    d["reserved"] = list(s)

def sort_dn_by_num(uid_dn: Tuple[str, str]):
    uid, dn = uid_dn
    if dn.isdigit():
        return (0, int(dn))
    return (1, dn.lower())

# ──────────────────────────────────────────────────────────────────────────────
# Admin-Kontakt-System (für alle Admin-Befehle)
# ──────────────────────────────────────────────────────────────────────────────
async def try_get_invite_link(guild: discord.Guild) -> str:
    """Versucht, einen Invite-Link zu ermitteln (vanity / bestehende Invites / neuen erstellen)."""
    try:
        if guild.vanity_url_code:
            return f"https://discord.gg/{guild.vanity_url_code}"
    except Exception:
        pass

    try:
        invites = await guild.invites()
        if invites:
            return invites[0].url
    except Exception:
        pass

    try:
        channel = guild.system_channel or next((c for c in guild.text_channels if c.permissions_for(guild.me).create_instant_invite), None)
        if channel:
            inv = await channel.create_invite(max_age=0, max_uses=0, reason="Admin-Kontakt DM")
            return inv.url
    except Exception:
        pass

    return "—"

class AdminContactModal(discord.ui.Modal, title="ADMIN kontaktieren"):
    def __init__(self, source_inter: discord.Interaction, command: str):
        super().__init__(timeout=180)
        self.source_inter = source_inter
        self.command = command
        self.text = discord.ui.TextInput(
            label="Warum kontaktierst du den Admin?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            placeholder="Beschreibe kurz dein Anliegen…"
        )
        self.add_item(self.text)

    async def on_submit(self, inter: discord.Interaction):
        guild = self.source_inter.guild
        invite = await try_get_invite_link(guild)
        embed = discord.Embed(title="🚨 ADMIN-Kontakt", color=discord.Color.red(), timestamp=now_dt())
        embed.add_field(name="Ausführer", value=f"{self.source_inter.user.mention} (`{self.source_inter.user.id}`)", inline=False)
        embed.add_field(name="Befehl", value=self.command, inline=False)
        embed.add_field(name="Datum & Uhrzeit", value=now_str(), inline=False)
        embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=False)
        embed.add_field(name="Server-Link", value=invite, inline=False)
        embed.add_field(name="Nachricht", value=self.text.value, inline=False)

        admins: List[discord.Member] = [m for m in guild.members if m.guild_permissions.administrator]
        sent = 0
        for a in admins:
            try:
                await a.send(embed=embed)
                sent += 1
            except Exception:
                pass
        await inter.response.send_message(f"✅ Admins benachrichtigt (DMs an {sent} Admin(s)).", ephemeral=True)

class AdminContactView(discord.ui.View):
    def __init__(self, source_inter: discord.Interaction, command: str):
        super().__init__(timeout=120)
        self.source_inter = source_inter
        self.command = command

    @discord.ui.button(label="📩 ADMIN kontaktieren", style=discord.ButtonStyle.danger)
    async def contact(self, inter: discord.Interaction, btn: discord.ui.Button):
        if inter.user.id != self.source_inter.user.id:
            return await inter.response.send_message("❌ Nur der ursprüngliche Nutzer kann diesen Button verwenden.", ephemeral=True)
        await inter.response.send_modal(AdminContactModal(self.source_inter, self.command))

async def require_admin(inter: discord.Interaction, command_name: str) -> bool:
    """Allen Admin-Befehlen vorschalten: zeigt Button/Modal an, wenn kein Admin."""
    if is_admin(inter):
        return True
    await inter.response.send_message(
        "❌ Dieser Befehl ist nur für Admins. Du kannst den Admin kontaktieren:",
        view=AdminContactView(inter, command_name),
        ephemeral=True
    )
    return False

# ──────────────────────────────────────────────────────────────────────────────
# DN-Liste
# ──────────────────────────────────────────────────────────────────────────────
async def update_dn_list(guild: discord.Guild):
    d = load_data(guild.id)
    ch_id = d["config"].get("dn_channel")
    if not ch_id:
        return
    ch: Optional[discord.TextChannel] = guild.get_channel(ch_id)
    if not ch:
        return

    # Aufbau: "1 - @User" etc. (DN - @User)
    if d["dienstnummern"]:
        lines = []
        for uid, dn in sorted(d["dienstnummern"].items(), key=sort_dn_by_num):
            member = guild.get_member(int(uid))
            lines.append(f"{dn} - {member.mention if member else f'<@{uid}>'}")
        desc = "\n".join(lines)
    else:
        desc = "Keine Dienstnummern vergeben."

    embed = discord.Embed(
        title="📋 Dienstnummern-Liste",
        description=desc,
        color=discord.Color.blurple(),
        timestamp=now_dt()
    )
    embed.set_footer(text=f"Stand: {now_str()}")

    # Vorherige Bot-Nachrichten entfernen
    try:
        async for msg in ch.history(limit=50):
            if msg.author == guild.me:
                await msg.delete()
    except discord.Forbidden:
        pass

    await ch.send(embed=embed)

# ──────────────────────────────────────────────────────────────────────────────
# Logging & Entscheidungen
# ──────────────────────────────────────────────────────────────────────────────
def set_or_replace_field(embed: discord.Embed, name: str, value: str, inline: bool = False):
    for idx, f in enumerate(embed.fields):
        if f.name == name:
            embed.set_field_at(idx, name=name, value=value, inline=inline)
            return
    embed.add_field(name=name, value=value, inline=inline)

async def pin_and_clean(channel: discord.TextChannel, message: discord.Message):
    try:
        await message.pin()
        # "pins_add"-Systemnachricht des Bots entfernen
        async for m in channel.history(limit=10):
            if m.type == discord.MessageType.pins_add and m.author == channel.guild.me:
                try:
                    await m.delete()
                except Exception:
                    pass
    except Exception:
        pass

async def post_log(
    inter: discord.Interaction,
    action: str,                            # "add" | "remove" | "edit" | "request" | "manual"
    target: Optional[discord.Member],
    nummer: Optional[str],
    prev_nummer: Optional[str] = None,
    note: Optional[str] = None,
    ping_roles: bool = True
) -> Optional[str]:
    """Erstellt ein Log mit Buttons (falls entscheidbar) und pingt Berechtigte."""
    guild = inter.guild
    d = load_data(guild.id)
    log_ch_id = d["config"].get("log_channel")
    if not log_ch_id:
        return None
    log_ch: Optional[discord.TextChannel] = guild.get_channel(log_ch_id)
    if not log_ch:
        return None

    log_id = str(uuid.uuid4())

    title_map = {
        "add": "🔔 Neue Dienstnummer vergeben (Antrag)",
        "remove": "🔔 Dienstnummer entfernen (Antrag)",
        "edit": "🔔 Dienstnummer bearbeiten (Antrag)",
        "request": "🔔 Dienstnummer-Anfrage (Mitarbeiter)",
        "manual": "🛠️ Manuelle Entscheidung"
    }

    embed = discord.Embed(
        title=title_map.get(action, "🔔 Aktion"),
        color=discord.Color.orange() if action != "manual" else discord.Color.gold(),
        timestamp=now_dt()
    )
    set_or_replace_field(embed, "Status", "⏳ Ausstehend" if action != "manual" else "—", inline=False)
    set_or_replace_field(embed, "Log-ID", f"`{log_id}`", inline=False)
    set_or_replace_field(embed, "Aktion", action, inline=True)
    set_or_replace_field(embed, "Datum & Uhrzeit", now_str(), inline=True)
    set_or_replace_field(embed, "Ausführer", f"{inter.user.mention} (`{inter.user.id}`)", inline=False)
    if target:
        set_or_replace_field(embed, "Betroffene Person", f"{target.mention} (`{target.id}`)", inline=False)
    if nummer:
        set_or_replace_field(embed, "Dienstnummer", f"**{nummer}**", inline=True)
    if prev_nummer:
        set_or_replace_field(embed, "Vorherige Dienstnummer", f"**{prev_nummer}**", inline=True)
    if note:
        set_or_replace_field(embed, "Hinweis", note, inline=False)
    set_or_replace_field(embed, "Server", f"{guild.name} (`{guild.id}`)", inline=False)
    set_or_replace_field(embed, "Kanal", f"{inter.channel.mention} (`{inter.channel.id}`)", inline=False)

    content = None
    if ping_roles:
        roles = [guild.get_role(rid) for rid in d["config"].get("roles", [])]
        mentions = " ".join(r.mention for r in roles if r)
        content = mentions if mentions else None

    view = None
    if action != "manual":
        view = DecisionView(
            guild_id=guild.id,
            log_id=log_id,
            action=action,
            requester_id=inter.user.id,
            target_id=(target.id if target else None),
            nummer=nummer,
            prev_nummer=prev_nummer
        )

    msg = await log_ch.send(content=content, embed=embed, view=view)

    # Nachrichtenlink ergänzen
    link = msg.jump_url
    set_or_replace_field(embed, "Nachrichtenlink", f"[Zur Log-Nachricht]({link})", inline=False)
    await msg.edit(embed=embed, view=view)

    # Log speichern
    d["logs"][log_id] = {
        "channel_id": log_ch.id,
        "message_id": msg.id,
        "message_link": link,
        "action": action,
        "requester_id": inter.user.id,
        "target_id": target.id if target else None,
        "number": nummer,
        "prev_number": prev_nummer,
        "status": "pending" if action != "manual" else "manual",
        "created_at": now_str(),
        "linked_log_id": None
    }
    save_data(guild.id, d)
    return log_id

class DecisionView(discord.ui.View):
    def __init__(self, guild_id: int, log_id: str, action: str, requester_id: int,
                 target_id: Optional[int], nummer: Optional[str], prev_nummer: Optional[str]):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.log_id = log_id
        self.action = action
        self.requester_id = requester_id
        self.target_id = target_id
        self.nummer = nummer
        self.prev_nummer = prev_nummer

    def _can_decide(self, inter: discord.Interaction) -> bool:
        d = load_data(self.guild_id)
        if inter.user.id == ADMIN_USER_ID or inter.user.guild_permissions.administrator:
            return True
        return any(r.id in d["config"].get("roles", []) for r in inter.user.roles)

    async def _accept_apply(self, inter: discord.Interaction, embed: discord.Embed):
        d = load_data(self.guild_id)
        guild = inter.guild

        if self.action in ("add", "request"):
            uid = self.target_id or self.requester_id
            if not self.nummer:
                return False, "Keine Dienstnummer im Log."
            if number_in_use(d, self.nummer):
                return False, f"DN **{self.nummer}** ist bereits vergeben."
            d["dienstnummern"][str(uid)] = self.nummer
            release_number(d, self.nummer)

        elif self.action == "remove":
            if self.target_id is None:
                return False, "Kein Zielbenutzer."
            curr = d["dienstnummern"].get(str(self.target_id))
            if not curr:
                return False, "Benutzer hat keine Dienstnummer."
            d["dienstnummern"].pop(str(self.target_id), None)

        elif self.action == "edit":
            if self.target_id is None or not self.nummer:
                return False, "Fehlende Daten."
            # Nur erlaubt, wenn entweder frei oder bereits dieselbe für diesen User
            if number_in_use(d, self.nummer) and d["dienstnummern"].get(str(self.target_id)) != self.nummer:
                return False, f"DN **{self.nummer}** ist bereits vergeben."
            d["dienstnummern"][str(self.target_id)] = self.nummer
            release_number(d, self.nummer)

        d["logs"][self.log_id]["status"] = "accepted"
        save_data(self.guild_id, d)
        await update_dn_list(guild)
        set_or_replace_field(embed, "Status", "✅ Akzeptiert", inline=False)
        embed.color = discord.Color.green()
        return True, None

    async def _reject_apply(self, inter: discord.Interaction, embed: discord.Embed):
        d = load_data(self.guild_id)
        if self.action in ("add", "request", "edit"):
            release_number(d, self.nummer)
        d["logs"][self.log_id]["status"] = "rejected"
        save_data(self.guild_id, d)
        set_or_replace_field(embed, "Status", "❌ Abgelehnt", inline=False)
        embed.color = discord.Color.red()
        return True, None

    @discord.ui.button(label="✅ Akzeptieren", style=discord.ButtonStyle.success)
    async def accept(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not self._can_decide(inter):
            return await inter.response.send_message("❌ Keine Berechtigung für diese Entscheidung.", ephemeral=True)
        embed = inter.message.embeds[0]
        status = next((f.value for f in embed.fields if f.name == "Status"), "")
        if "Akzeptiert" in status or "Abgelehnt" in status:
            return await inter.response.send_message("❌ Diese Anfrage ist bereits entschieden.", ephemeral=True)

        ok, err = await self._accept_apply(inter, embed)
        if not ok:
            return await inter.response.send_message(f"❌ Aktion fehlgeschlagen: {err}", ephemeral=True)
        await inter.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="❌ Ablehnen", style=discord.ButtonStyle.danger)
    async def reject(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not self._can_decide(inter):
            return await inter.response.send_message("❌ Keine Berechtigung für diese Entscheidung.", ephemeral=True)
        embed = inter.message.embeds[0]
        status = next((f.value for f in embed.fields if f.name == "Status"), "")
        if "Akzeptiert" in status or "Abgelehnt" in status:
            return await inter.response.send_message("❌ Diese Anfrage ist bereits entschieden.", ephemeral=True)

        ok, err = await self._reject_apply(inter, embed)
        if not ok:
            return await inter.response.send_message(f"❌ Aktion fehlgeschlagen: {err}", ephemeral=True)
        await inter.response.edit_message(embed=embed, view=None)

# ──────────────────────────────────────────────────────────────────────────────
# Events
# ──────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
        print(f"✅ Slash-Befehle synchronisiert. Eingeloggt als {bot.user}.")
    except Exception as e:
        print(f"❌ Sync-Fehler: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Slash Commands
# ──────────────────────────────────────────────────────────────────────────────

# ADMIN: /konfiguriere
@bot.tree.command(name="konfiguriere", description="Setzt Log-Kanal, DN-Kanal sowie Berechtigte- & Mitarbeiter-Rollen.")
async def konfiguriere(inter: discord.Interaction, log_channel: discord.TextChannel, dn_channel: discord.TextChannel):
    if not await require_admin(inter, "/konfiguriere"):
        return

    class RoleSelect(discord.ui.Select):
        def __init__(self, placeholder: str, cid: str):
            options = [discord.SelectOption(label=r.name, value=str(r.id))
                       for r in inter.guild.roles if not r.is_default()]
            super().__init__(placeholder=placeholder, options=options, min_values=0, max_values=min(25, len(options)), custom_id=cid)
        async def callback(self, i: discord.Interaction):
            if i.user.id != inter.user.id:
                return await i.response.send_message("❌ Nur der Aufrufer kann konfigurieren.", ephemeral=True)
            if self.custom_id == "berechtigte":
                self.view.state_berechtigte = [int(v) for v in self.values]
            else:
                self.view.state_mitarbeiter = [int(v) for v in self.values]
            await i.response.defer()

    class SaveBtn(discord.ui.Button):
        def __init__(self):
            super().__init__(label="💾 Speichern", style=discord.ButtonStyle.success)
        async def callback(self, i: discord.Interaction):
            d = load_data(inter.guild.id)
            d["config"]["log_channel"] = log_channel.id
            d["config"]["dn_channel"] = dn_channel.id
            d["config"]["roles"] = self.view.state_berechtigte
            d["config"]["mitarbeiter"] = self.view.state_mitarbeiter
            save_data(inter.guild.id, d)

            roles = [inter.guild.get_role(rid) for rid in d["config"]["roles"]]
            mitarbeiter = [inter.guild.get_role(rid) for rid in d["config"]["mitarbeiter"]]

            # Info-Embed im LOG-Kanal, anpinnen, Systemmeldung löschen, plus Befehlsübersicht
            embed = discord.Embed(title="⚙️ Konfiguration gespeichert", color=discord.Color.green(), timestamp=now_dt())
            embed.add_field(name="📢 Log-Kanal", value=log_channel.mention, inline=True)
            embed.add_field(name="📋 DN-Kanal", value=dn_channel.mention, inline=True)
            embed.add_field(name="👮 Berechtigte Rollen", value=", ".join(r.mention for r in roles if r) or "Keine", inline=False)
            embed.add_field(name="👷 Mitarbeiter Rollen", value=", ".join(r.mention for r in mitarbeiter if r) or "Keine", inline=False)
            embed.add_field(
                name="📖 Befehlsübersicht",
                value=(
                    "**ADMIN**:\n"
                    "• `/konfiguriere` – Log/DN-Kanal & Rollen setzen\n"
                    "• `/alle_löschen` – Alle DNs & Reservierungen löschen\n"
                    "• `/aktion_bearbeiten` – Entscheidung abgeschlossener Logs ändern\n\n"
                    "**BERECHTIGTE**:\n"
                    "• `/neue_dienstnummer` – DN für Nutzer beantragen (DN wird reserviert)\n"
                    "• `/entferne_dienstnummer` – Entfernung der DN beantragen\n"
                    "• `/bearbeite_dienstnummer` – Änderung der DN beantragen (neue DN reserviert)\n\n"
                    "**MITARBEITER**:\n"
                    "• `/dienstnummer_anfragen` – Eigene DN anfragen (reserviert)\n\n"
                    "**ALLE**:\n"
                    "• `/dienstnummer` – Eigene/fremde DN anzeigen\n"
                    "• `/dienstnummer_info` – Besitzer einer DN anzeigen\n\n"
                    "ℹ️ Hinweise:\n"
                    "• Anträge werden im Log entschieden; erst dann werden Änderungen wirksam.\n"
                    "• Neue/angefragte/edits DNs sind bis zur Entscheidung blockiert (reserviert)."
                ),
                inline=False
            )
            log_ch = inter.guild.get_channel(d["config"]["log_channel"])
            info_msg = await log_ch.send(embed=embed)
            await pin_and_clean(log_ch, info_msg)

            # DN-Liste aktualisieren
            await update_dn_list(inter.guild)

            await i.response.edit_message(content="✅ Konfiguration gespeichert.", view=None)

    class ConfigView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=180)
            self.state_berechtigte = []
            self.state_mitarbeiter = []
            self.add_item(RoleSelect("👉 Berechtigte Rollen wählen…", "berechtigte"))
            self.add_item(RoleSelect("👉 Mitarbeiter Rollen wählen…", "mitarbeiter"))
            self.add_item(SaveBtn())

    await inter.response.send_message("⚙️ Bitte wähle die Rollen:", view=ConfigView(), ephemeral=True)

# ADMIN: /alle_löschen
@bot.tree.command(name="alle_löschen", description="Löscht alle Dienstnummern und Reservierungen (ADMIN).")
async def alle_loeschen(inter: discord.Interaction):
    if not await require_admin(inter, "/alle_löschen"):
        return
    d = load_data(inter.guild.id)
    d["dienstnummern"] = {}
    d["reserved"] = []
    save_data(inter.guild.id, d)
    await update_dn_list(inter.guild)
    await inter.response.send_message("✅ Alle Dienstnummern & Reservierungen gelöscht.", ephemeral=True)

# ADMIN: /aktion_bearbeiten
@bot.tree.command(name="aktion_bearbeiten", description="Entscheidung eines abgeschlossenen Logs ändern (verlinkt altes & neues Log).")
async def aktion_bearbeiten(inter: discord.Interaction, log_id: str, entscheidung: str):
    if not await require_admin(inter, "/aktion_bearbeiten"):
        return

    entscheidung = entscheidung.lower().strip()
    if entscheidung not in ("akzeptieren", "ablehnen"):
        return await inter.response.send_message("❌ Ungültig. Erlaubt: `akzeptieren` oder `ablehnen`.", ephemeral=True)

    d = load_data(inter.guild.id)
    old = d["logs"].get(log_id)
    if not old:
        return await inter.response.send_message("❌ Log-ID nicht gefunden.", ephemeral=True)
    if old["status"] == "pending":
        return await inter.response.send_message("❌ Dieses Log ist noch ausstehend. Bitte zuerst normal entscheiden.", ephemeral=True)

    log_ch: Optional[discord.TextChannel] = inter.guild.get_channel(old["channel_id"])
    if not log_ch:
        return await inter.response.send_message("❌ Log-Kanal existiert nicht.", ephemeral=True)
    old_msg = await log_ch.fetch_message(old["message_id"])
    old_embed = old_msg.embeds[0]

    target = inter.guild.get_member(int(old["target_id"])) if old.get("target_id") else None
    nummer = old.get("number")
    prev = old.get("prev_number")

    # Neues, manuelles Log anlegen
    new_log_id = await post_log(
        inter,
        action="manual",
        target=target,
        nummer=nummer,
        prev_nummer=prev,
        note=f"Änderung der Entscheidung von Log `{log_id}`: alt `{old['status']}` → neu `{entscheidung}`.",
        ping_roles=True
    )
    if not new_log_id:
        return await inter.response.send_message("❌ Neues Log konnte nicht erstellt werden.", ephemeral=True)

    # neue Logdaten laden
    d = load_data(inter.guild.id)
    new_entry = d["logs"][new_log_id]
    new_link = new_entry.get("message_link", "—")

    # Verlinkungen ergänzen (beide Richtungen, mit Nachrichtenlink)
    set_or_replace_field(old_embed, "Neue Entscheidung (Log)", f"`{new_log_id}` – [Nachricht]({new_link})", inline=False)
    old_embed.color = discord.Color.gold()
    await old_msg.edit(embed=old_embed)

    new_ch = inter.guild.get_channel(new_entry["channel_id"])
    new_msg = await new_ch.fetch_message(new_entry["message_id"])
    new_embed = new_msg.embeds[0]
    set_or_replace_field(new_embed, "Altes Log", f"`{log_id}` – [Nachricht]({old.get('message_link', old_msg.jump_url)})", inline=False)
    await new_msg.edit(embed=new_embed)

    # Verlinkung speichern
    d["logs"][log_id]["linked_log_id"] = new_log_id
    d["logs"][new_log_id]["linked_log_id"] = log_id

    # Inhaltliche Änderung anwenden, falls Status wirklich geändert wird
    want_accept = (entscheidung == "akzeptieren")
    was_accepted = (old["status"] == "accepted")

    if want_accept != was_accepted:
        action = old["action"]
        if want_accept and old["status"] == "rejected":
            # akzeptieren nachträglich
            if action in ("add", "request"):
                uid = old["target_id"] or old["requester_id"]
                if nummer and not number_in_use(d, nummer) and not number_reserved(d, nummer):
                    d["dienstnummern"][str(uid)] = nummer
            elif action == "remove":
                uid = old.get("target_id")
                if uid:
                    d["dienstnummern"].pop(str(uid), None)
            elif action == "edit":
                uid = old.get("target_id")
                if uid and nummer and not (number_in_use(d, nummer) and d["dienstnummern"].get(str(uid)) != nummer):
                    d["dienstnummern"][str(uid)] = nummer
            d["logs"][new_log_id]["status"] = "accepted"
            save_data(inter.guild.id, d)
            await update_dn_list(inter.guild)
            await inter.response.send_message(f"✅ Entscheidung geändert → jetzt **akzeptiert**. Neues Log `{new_log_id}`.", ephemeral=True)

        elif (not want_accept) and old["status"] == "accepted":
            # ablehnen nachträglich → rückgängig machen
            action = old["action"]
            if action in ("add", "request"):
                uid = old["target_id"] or old["requester_id"]
                if d["dienstnummern"].get(str(uid)) == nummer:
                    d["dienstnummern"].pop(str(uid), None)
            elif action == "remove":
                uid = old.get("target_id")
                if uid and prev:
                    d["dienstnummern"][str(uid)] = prev
            elif action == "edit":
                uid = old.get("target_id")
                if uid and prev:
                    d["dienstnummern"][str(uid)] = prev
            d["logs"][new_log_id]["status"] = "rejected"
            save_data(inter.guild.id, d)
            await update_dn_list(inter.guild)
            await inter.response.send_message(f"✅ Entscheidung geändert → jetzt **abgelehnt**. Neues Log `{new_log_id}`.", ephemeral=True)
    else:
        save_data(inter.guild.id, d)
        await inter.response.send_message(f"ℹ️ Entscheidung dokumentiert (keine inhaltliche Änderung). Neues Log `{new_log_id}`.", ephemeral=True)

# BERECHTIGTE: /neue_dienstnummer
@bot.tree.command(name="neue_dienstnummer", description="Beantragt, einem Nutzer eine Dienstnummer zuzuweisen (DN wird reserviert).")
async def neue_dienstnummer(inter: discord.Interaction, user: discord.Member, nummer: str):
    if not is_berechtigt(inter):
        return await inter.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    d = load_data(inter.guild.id)
    if number_in_use(d, nummer) or number_reserved(d, nummer):
        return await inter.response.send_message("❌ Diese Dienstnummer ist bereits vergeben oder reserviert.", ephemeral=True)
    reserve_number(d, nummer)
    save_data(inter.guild.id, d)
    log_id = await post_log(inter, "add", target=user, nummer=nummer, ping_roles=True)
    await inter.response.send_message(f"✅ Antrag erstellt (Log `{log_id}`) – DN **{nummer}** bis Entscheidung reserviert.", ephemeral=True)

# BERECHTIGTE: /entferne_dienstnummer
@bot.tree.command(name="entferne_dienstnummer", description="Beantragt, die Dienstnummer eines Nutzers zu entfernen.")
async def entferne_dienstnummer(inter: discord.Interaction, user: discord.Member):
    if not is_berechtigt(inter):
        return await inter.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    d = load_data(inter.guild.id)
    curr = d["dienstnummern"].get(str(user.id))
    if not curr:
        return await inter.response.send_message("❌ Dieser Nutzer hat keine Dienstnummer.", ephemeral=True)
    log_id = await post_log(inter, "remove", target=user, nummer=curr, ping_roles=True)
    await inter.response.send_message(f"✅ Antrag zum Entfernen erstellt (Log `{log_id}`).", ephemeral=True)

# BERECHTIGTE: /bearbeite_dienstnummer
@bot.tree.command(name="bearbeite_dienstnummer", description="Beantragt, die Dienstnummer eines Nutzers zu ändern (neue DN wird reserviert).")
async def bearbeite_dienstnummer(inter: discord.Interaction, user: discord.Member, neue_nummer: str):
    if not is_berechtigt(inter):
        return await inter.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    d = load_data(inter.guild.id)
    curr = d["dienstnummern"].get(str(user.id))
    if not curr:
        return await inter.response.send_message("❌ Dieser Nutzer hat keine Dienstnummer.", ephemeral=True)
    if neue_nummer == curr:
        return await inter.response.send_message("ℹ️ Neue DN ist identisch mit der aktuellen.", ephemeral=True)
    if number_in_use(d, neue_nummer) or number_reserved(d, neue_nummer):
        return await inter.response.send_message("❌ Diese neue DN ist bereits vergeben oder reserviert.", ephemeral=True)
    reserve_number(d, neue_nummer)
    save_data(inter.guild.id, d)
    log_id = await post_log(inter, "edit", target=user, nummer=neue_nummer, prev_nummer=curr, ping_roles=True)
    await inter.response.send_message(f"✅ Änderungsantrag erstellt (Log `{log_id}`) – **{neue_nummer}** reserviert.", ephemeral=True)

# MITARBEITER: /dienstnummer_anfragen
@bot.tree.command(name="dienstnummer_anfragen", description="Mitarbeiter fragt eine Dienstnummer an (wird reserviert).")
async def dienstnummer_anfragen(inter: discord.Interaction, nummer: str):
    if not is_mitarbeiter(inter):
        return await inter.response.send_message("❌ Nur Mitarbeiter dürfen diesen Befehl nutzen.", ephemeral=True)
    d = load_data(inter.guild.id)
    if number_in_use(d, nummer) or number_reserved(d, nummer):
        return await inter.response.send_message("❌ Diese DN ist bereits vergeben oder reserviert.", ephemeral=True)
    reserve_number(d, nummer)
    save_data(inter.guild.id, d)
    log_id = await post_log(inter, "request", target=inter.user, nummer=nummer, ping_roles=True)
    await inter.response.send_message(f"✅ Anfrage erstellt (Log `{log_id}`) – **{nummer}** bis Entscheidung reserviert.", ephemeral=True)

# ALLE: /dienstnummer (optional: anderer Nutzer)
@bot.tree.command(name="dienstnummer", description="Zeigt deine oder die Dienstnummer eines Nutzers.")
async def cmd_dienstnummer(inter: discord.Interaction, user: Optional[discord.Member] = None):
    u = user or inter.user
    d = load_data(inter.guild.id)
    dn = d["dienstnummern"].get(str(u.id))
    if dn:
        await inter.response.send_message(f"📋 {u.mention} → **{dn}**", ephemeral=True)
    else:
        await inter.response.send_message(f"❌ {u.mention} hat keine Dienstnummer.", ephemeral=True)

# ALLE: /dienstnummer_info (DN → Nutzer)
@bot.tree.command(name="dienstnummer_info", description="Zeigt Infos zu einer Dienstnummer.")
async def dienstnummer_info(inter: discord.Interaction, nummer: str):
    d = load_data(inter.guild.id)
    owner_id = next((uid for uid, dn in d["dienstnummern"].items() if dn == nummer), None)
    if not owner_id:
        return await inter.response.send_message("❌ Diese Dienstnummer ist nicht vergeben.", ephemeral=True)
    member = inter.guild.get_member(int(owner_id))
    await inter.response.send_message(f"ℹ️ Dienstnummer **{nummer}** gehört zu {member.mention if member else f'<@{owner_id}>'}.", ephemeral=True)

# ──────────────────────────────────────────────────────────────────────────────
# Start
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN nicht gesetzt.")
    bot.run(TOKEN)
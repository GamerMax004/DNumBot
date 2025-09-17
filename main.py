import discord
from discord import app_commands
from discord.ext import commands
import os
import datetime
import sqlite3

# ---------------- Einstellungen ----------------
ADMIN_IDS = [1211683189186105434]  # IDs der Admins
LOG_CHANNEL_ID = 1417589029326557274  # Fester Log-Kanal
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Datenbank ----------------
db = sqlite3.connect("bot.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS permissions (
    role_id TEXT,
    command TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS message_counts (
    user_id TEXT PRIMARY KEY,
    count INTEGER
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS warns (
    user_id TEXT,
    moderator_id TEXT,
    reason TEXT,
    timestamp TEXT
)
""")
db.commit()

# ---------------- Hilfsfunktionen ----------------
def format_datetime_de(dt: datetime.datetime) -> str:
    return dt.strftime("%d.%m.%Y %H:%M")

def log_embed(title: str, description: str, user: discord.Member, **fields):
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.from_str("#5B664E"),
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_footer(text=f"ID: {user.id} | {format_datetime_de(datetime.datetime.utcnow())}")
    for name, value in fields.items():
        embed.add_field(name=name, value=value, inline=False)
    return embed

async def send_log(interaction: discord.Interaction, title: str, description: str, **fields):
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        return
    embed = log_embed(title, description, interaction.user, **fields)
    await log_channel.send(embed=embed)
    await update_command_overview(interaction.guild)

def check_permissions(user: discord.Member, command: str):
    cursor.execute("SELECT role_id FROM permissions WHERE command=?", (command,))
    allowed_roles = [int(r[0]) for r in cursor.fetchall()]
    for role in user.roles:
        if role.id in allowed_roles:
            return True
    return False

# ---------------- Übersicht Befehle ----------------
async def update_command_overview(guild: discord.Guild):
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        return

    all_commands_list = ["set_waitting_time", "whois", "announcement", "warn", "unwarn", "kick", "ban", "timeout"]

    embed = discord.Embed(
        title="📋 Befehlsübersicht",
        description="Welche Rollen welche Befehle ausführen dürfen:",
        color=discord.Color.from_str("#5B664E"),
        timestamp=datetime.datetime.utcnow()
    )

    for cmd in all_commands_list:
        cursor.execute("SELECT role_id FROM permissions WHERE command=?", (cmd,))
        roles = cursor.fetchall()
        allowed_roles = [f"<@&{int(r[0])}>" for r in roles]
        embed.add_field(
            name=f"/{cmd}",
            value="\n".join(allowed_roles) if allowed_roles else "Keine",
            inline=False
        )

    pins = await log_channel.pins()
    overview = None
    for msg in pins:
        if msg.author == bot.user and msg.embeds and msg.embeds[0].title == "📋 Befehlsübersicht":
            overview = msg
            break

    if overview:
        await overview.edit(embed=embed)
    else:
        msg = await log_channel.send(embed=embed)
        await msg.pin()

# ---------------- Dropdown ----------------
class CommandDropdown(discord.ui.Select):
    def __init__(self, role: discord.Role, current_perms: list[str]):
        all_commands_list = ["set_waitting_time", "whois", "announcement", "warn", "unwarn", "kick", "ban", "timeout"]
        options = []
        for cmd in all_commands_list:
            options.append(discord.SelectOption(
                label=cmd,
                value=cmd,
                default=(cmd in current_perms)
            ))
        super().__init__(placeholder="Wähle Befehle aus...", min_values=0, max_values=len(all_commands_list), options=options)
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        cursor.execute("DELETE FROM permissions WHERE role_id=?", (str(self.role.id),))
        for val in self.values:
            cursor.execute("INSERT INTO permissions (role_id, command) VALUES (?, ?)", (str(self.role.id), val))
        db.commit()

        await interaction.response.send_message(
            f"✅ Befehle für {self.role.mention} gesetzt: {', '.join(self.values) if self.values else 'Keine'}",
            ephemeral=True
        )
        await send_log(interaction, "Konfiguration geändert", f"Befehle für {self.role.mention} angepasst.", Befehle=", ".join(self.values))

class CommandDropdownView(discord.ui.View):
    def __init__(self, role: discord.Role, current_perms: list[str]):
        super().__init__()
        self.add_item(CommandDropdown(role, current_perms))

# ---------------- Commands ----------------
@bot.tree.command(name="configure", description="Konfiguriere Befehle für eine Rolle")
@app_commands.describe(role="Die Rolle, die konfiguriert werden soll")
async def configure(interaction: discord.Interaction, role: discord.Role):
    if interaction.user.id not in ADMIN_IDS:
        return await interaction.response.send_message("❌ Keine Berechtigung!", ephemeral=True)

    cursor.execute("SELECT command FROM permissions WHERE role_id=?", (str(role.id),))
    current_perms = [row[0] for row in cursor.fetchall()]
    view = CommandDropdownView(role, current_perms)
    await interaction.response.send_message(f"Befehle für {role.mention} konfigurieren:", view=view, ephemeral=True)

# ---------- set_waitting_time ----------
class WaitingTimeModal(discord.ui.Modal, title="Wartezeit einstellen"):
    date = discord.ui.TextInput(label="Datum (TT.MM.JJJJ)", style=discord.TextStyle.short, required=True)
    time = discord.ui.TextInput(label="Uhrzeit (HH:MM)", style=discord.TextStyle.short, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            dt = datetime.datetime.strptime(f"{self.date.value} {self.time.value}", "%d.%m.%Y %H:%M")
        except Exception:
            return await interaction.response.send_message("❌ Ungültiges Datum oder Uhrzeit!", ephemeral=True)

        formatted = format_datetime_de(dt)
        await interaction.response.send_message(f"⏰ Wartezeit gesetzt auf {formatted}")
        await send_log(interaction, "Wartezeit gesetzt", f"Wartezeit eingestellt auf {formatted}")

@bot.tree.command(name="set_waitting_time", description="Setzt die Wartezeit")
async def set_waitting_time(interaction: discord.Interaction):
    if not check_permissions(interaction.user, "set_waitting_time"):
        return await interaction.response.send_message("❌ Keine Berechtigung!", ephemeral=True)
    await interaction.response.send_modal(WaitingTimeModal())

# ---------- Nachrichten-Zähler ----------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    cursor.execute("SELECT count FROM message_counts WHERE user_id=?", (str(message.author.id),))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE message_counts SET count=? WHERE user_id=?", (row[0] + 1, str(message.author.id)))
    else:
        cursor.execute("INSERT INTO message_counts (user_id, count) VALUES (?, ?)", (str(message.author.id), 1))
    db.commit()
    await bot.process_commands(message)

# ---------- whois ----------
@bot.tree.command(name="whois", description="Infos über einen User")
@app_commands.describe(user="Der User")
async def whois(interaction: discord.Interaction, user: discord.User):
    if not check_permissions(interaction.user, "whois"):
        return await interaction.response.send_message("❌ Keine Berechtigung!", ephemeral=True)

    member = interaction.guild.get_member(user.id)
    if not member:
        return await interaction.response.send_message("❌ User nicht auf dem Server!", ephemeral=True)

    roles_sorted = sorted(member.roles[1:], key=lambda r: r.position, reverse=True)
    roles_text = "\n".join([r.mention for r in roles_sorted]) if roles_sorted else "Keine Rollen"

    cursor.execute("SELECT count FROM message_counts WHERE user_id=?", (str(member.id),))
    row = cursor.fetchone()
    msg_count = row[0] if row else 0

    embed = discord.Embed(
        title=f"User Info: {member}",
        color=discord.Color.from_str("#5B664E"),
        timestamp=datetime.datetime.utcnow()
    )
    embed.add_field(name="ID", value=member.id, inline=False)
    embed.add_field(name="Joined", value=format_datetime_de(member.joined_at), inline=False)
    embed.add_field(name="Registered", value=format_datetime_de(member.created_at), inline=False)
    embed.add_field(name="Nachrichten gesendet", value=str(msg_count), inline=False)
    embed.add_field(name="Rollen", value=roles_text, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"ID: {member.id} | {format_datetime_de(datetime.datetime.utcnow())}")

    await interaction.response.send_message(embed=embed)
    await send_log(interaction, "Whois ausgeführt", f"Abfrage von {member}", Ziel=member.mention)

# ---------- announcement ----------
class AnnouncementModal(discord.ui.Modal, title="Ankündigung erstellen"):
    def __init__(self, ping, kanal):
        super().__init__()
        self.ping = ping
        self.kanal = kanal
        self.title_field = discord.ui.TextInput(label="Titel", style=discord.TextStyle.short, required=True)
        self.author_field = discord.ui.TextInput(label="Autor (optional)", style=discord.TextStyle.short, required=False)
        self.text_field = discord.ui.TextInput(label="Text", style=discord.TextStyle.paragraph, required=True)
        self.image_field = discord.ui.TextInput(label="Bild-URL", style=discord.TextStyle.short, required=False)
        self.add_item(self.title_field)
        self.add_item(self.author_field)
        self.add_item(self.text_field)
        self.add_item(self.image_field)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=self.title_field.value,
            description=self.text_field.value,
            color=discord.Color.from_str("#5B664E"),
            timestamp=datetime.datetime.utcnow()
        )
        if self.author_field.value:
            embed.set_author(name=self.author_field.value)
        if self.image_field.value:
            embed.set_image(url=self.image_field.value)
        embed.set_footer(
            text=f"ID: {interaction.user.id} | {format_datetime_de(datetime.datetime.utcnow())}",
            icon_url=interaction.user.display_avatar.url
        )
        msg_content = self.ping.mention if self.ping else ""
        await self.kanal.send(content=msg_content, embed=embed)
        await interaction.response.send_message("✅ Ankündigung gesendet!", ephemeral=True)
        await send_log(interaction, "Ankündigung erstellt", f"In {self.kanal.mention} mit Ping {msg_content}")

@bot.tree.command(name="announcement", description="Macht eine Ankündigung")
@app_commands.describe(ping="Wen pingen?", kanal="Wo posten?")
async def announcement(interaction: discord.Interaction, ping: discord.Role, kanal: discord.TextChannel):
    if not check_permissions(interaction.user, "announcement"):
        return await interaction.response.send_message("❌ Keine Berechtigung!", ephemeral=True)
    modal = AnnouncementModal(ping, kanal)
    await interaction.response.send_modal(modal)

# ---------------- Moderation Commands ----------------
# /warn
@bot.tree.command(name="warn", description="Verwarnt einen User")
@app_commands.describe(user="User", reason="Grund")
async def warn_cmd(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not check_permissions(interaction.user, "warn"):
        return await interaction.response.send_message("❌ Keine Berechtigung!", ephemeral=True)

    timestamp = datetime.datetime.utcnow().isoformat()
    cursor.execute(
        "INSERT INTO warns (user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?)",
        (str(user.id), str(interaction.user.id), reason, timestamp)
    )
    db.commit()

    # DM an User
    try:
        embed_dm = discord.Embed(
            title="⚠️ Verwarnung",
            description=f"> Grund: {reason}",
            color=discord.Color.from_str("#5B664E"),
            timestamp=datetime.datetime.utcnow()
        )
        embed_dm.set_footer(text=f"Von {interaction.user} | {format_datetime_de(datetime.datetime.utcnow())}",
                            icon_url=interaction.user.display_avatar.url)
        await user.send(embed=embed_dm)
    except:
        pass

    await interaction.response.send_message(f"✅ {user.mention} wurde verwarnt.", ephemeral=True)
    await send_log(interaction, "Warn", f"{user.mention} wurde verwarnt", moderator=str(interaction.user), Grund=reason)

# /unwarn
@bot.tree.command(name="unwarn", description="Entfernt eine Verwarnung von einem User")
@app_commands.describe(user="User")
async def unwarn_cmd(interaction: discord.Interaction, user: discord.Member):
    if not check_permissions(interaction.user, "unwarn"):
        return await interaction.response.send_message("❌ Keine Berechtigung!", ephemeral=True)

    cursor.execute("SELECT rowid, reason, timestamp FROM warns WHERE user_id=? ORDER BY timestamp", (str(user.id),))
    warns = cursor.fetchall()
    if not warns:
        return await interaction.response.send_message("Keine Verwarnungen vorhanden.", ephemeral=True)

    options = [discord.SelectOption(label=f"{w[1]} | {format_datetime_de(datetime.datetime.fromisoformat(w[2]))}", value=str(w[0])) for w in warns]

    class UnwarnSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="Wähle Verwarnung zum Entfernen", min_values=1, max_values=1, options=options)

        async def callback(self, intr: discord.Interaction):
            cursor.execute("DELETE FROM warns WHERE rowid=?", (self.values[0],))
            db.commit()
            await intr.response.send_message("✅ Verwarnung entfernt.", ephemeral=True)
            await send_log(interaction, "Unwarn", f"Verwarnung von {user.mention} entfernt", moderator=str(interaction.user))

    view = discord.ui.View()
    view.add_item(UnwarnSelect())
    await interaction.response.send_message("Wähle eine Verwarnung zum Entfernen:", view=view, ephemeral=True)

# /kick
@bot.tree.command(name="kick", description="Kickt einen User")
@app_commands.describe(user="User", reason="Grund")
async def kick_cmd(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not check_permissions(interaction.user, "kick"):
        return await interaction.response.send_message("❌ Keine Berechtigung!", ephemeral=True)
    try:
        await user.send(f"⛔ Du wurdest gekickt!\nGrund: {reason}")
    except:
        pass
    await user.kick(reason=f"{reason} (von {interaction.user})")
    await interaction.response.send_message(f"✅ {user.mention} gekickt.", ephemeral=True)
    await send_log(interaction, "Kick", f"{user.mention} gekickt", moderator=str(interaction.user), Grund=reason)

# /ban
@bot.tree.command(name="ban", description="Bannt einen User")
@app_commands.describe(user="User", reason="Grund")
async def ban_cmd(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not check_permissions(interaction.user, "ban"):
        return await interaction.response.send_message("❌ Keine Berechtigung!", ephemeral=True)
    try:
        await user.send(f"⛔ Du wurdest gebannt!\nGrund: {reason}")
    except:
        pass
    await user.ban(reason=f"{reason} (von {interaction.user})")
    await interaction.response.send_message(f"✅ {user.mention} gebannt.", ephemeral=True)
    await send_log(interaction, "Ban", f"{user.mention} gebannt", moderator=str(interaction.user), Grund=reason)

# /timeout
@bot.tree.command(name="timeout", description="Setzt einen User ins Timeout")
@app_commands.describe(user="User", duration="Dauer in Minuten")
async def timeout_cmd(interaction: discord.Interaction, user: discord.Member, duration: int):
    if not check_permissions(interaction.user, "timeout"):
        return await interaction.response.send_message("❌ Keine Berechtigung!", ephemeral=True)
    try:
        await user.timeout(datetime.timedelta(minutes=duration), reason=f"Von {interaction.user}")
        try:
            await user.send(f"⏱ Du wurdest für {duration} Minuten ins Timeout gesetzt. Bei Fragen wende dich an {interaction.user}.")
        except:
            pass
        await interaction.response.send_message(f"✅ {user.mention} ins Timeout gesetzt.", ephemeral=True)
        await send_log(interaction, "Timeout", f"{user.mention} ins Timeout gesetzt ({duration} min)", moderator=str(interaction.user))
    except Exception as e:
        await interaction.response.send_message(f"❌ Fehler: {e}", ephemeral=True)

# ---------------- Start ----------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Bot online als {bot.user}")
    for guild in bot.guilds:
        await update_command_overview(guild)

bot.run(os.getenv("TOKEN"))
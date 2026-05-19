import discord
import io
import os
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import aiohttp

# ──────────────────────────────────────────
# ⚙️  CONFIGURATION — modifie ces valeurs
# ──────────────────────────────────────────
TOKEN = os.getenv("TOKEN")

WATERMARK_TEXT       = "DLP WORKS"       # Texte watermark
WATERMARK_OPACITY    = 0                # 0=invisible, 100=opaque (recommandé: 15-30)
WATERMARK_FONT_SIZE  = 80               # Taille du texte watermark

LOGO_FILE            = "logo.png"        # Fichier de ton logo
LOGO_SIZE_PERCENT    = 11               # Taille du logo en % de la largeur de l'image
LOGO_MARGIN          = 20               # Marge depuis le bord (pixels)

OUTPUT_QUALITY       = 100               # Qualité JPEG export (95-100 pour HDR)
# ──────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def add_watermark(image_bytes: bytes) -> bytes:
    """Ajoute le watermark texte + logo sur l'image, préserve la qualité."""
    
    # Ouvrir l'image originale
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = img.size

    # ── 1. WATERMARK TEXTE (diagonal, très transparent) ──────────────────
    txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)

    # Police : on essaie de charger une police système, sinon police par défaut
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", WATERMARK_FONT_SIZE)
    except:
        try:
            font = ImageFont.truetype("arial.ttf", WATERMARK_FONT_SIZE)
        except:
            font = ImageFont.load_default()

    # Mesure du texte
    bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
    txt_w = bbox[2] - bbox[0]
    txt_h = bbox[3] - bbox[1]

    # Créer une image temporaire pour le texte rotatif
    padding = 20
    txt_img = Image.new("RGBA", (txt_w + padding*2, txt_h + padding*2), (255, 255, 255, 0))
    txt_draw = ImageDraw.Draw(txt_img)
    txt_draw.text((padding, padding), WATERMARK_TEXT, font=font,
                  fill=(255, 255, 255, int(255 * WATERMARK_OPACITY / 100)))

    # Rotation et répétition en grille sur toute l'image
    txt_rotated = txt_img.rotate(30, expand=True)
    tw, th = txt_rotated.size
    for y in range(-th, height + th, th + 60):
        for x in range(-tw, width + tw, tw + 40):
            txt_layer.paste(txt_rotated, (x, y), txt_rotated)

    img = Image.alpha_composite(img, txt_layer)

    # ── 2. LOGO EN BAS À DROITE ───────────────────────────────────────────
    if os.path.exists(LOGO_FILE):
        logo = Image.open(LOGO_FILE).convert("RGBA")
        
        # Redimensionner le logo selon le pourcentage défini
        logo_width = int(width * LOGO_SIZE_PERCENT / 100)
        logo_ratio = logo_width / logo.size[0]
        logo_height = int(logo.size[1] * logo_ratio)
        logo = logo.resize((logo_width, logo_height), Image.LANCZOS)

        # Position bas-droite avec marge
        pos_x = width - logo_width - LOGO_MARGIN
        pos_y = height - logo_height - LOGO_MARGIN
        img.paste(logo, (pos_x, pos_y), logo)

    # ── 3. EXPORT en conservant la qualité maximale ───────────────────────
    output = io.BytesIO()
    img_rgb = img.convert("RGB")  # JPEG ne supporte pas RGBA
    img_rgb.save(output, format="JPEG", quality=OUTPUT_QUALITY,
                 subsampling=0, optimize=True)
    output.seek(0)
    return output.read()


@client.event
async def on_ready():
    print(f"✅ Bot connecté : {client.user}")


@client.event
async def on_message(message):
    # Ignorer les messages du bot lui-même
    if message.author == client.user:
        return

    # Vérifier s'il y a des images en pièce jointe
    images = [a for a in message.attachments
              if a.content_type and a.content_type.startswith("image/")]

    if not images:
        return  # Pas d'image → on ne fait rien

    await message.channel.typing()

    files_to_send = []
    async with aiohttp.ClientSession() as session:
        for attachment in images:
            # Télécharger l'image
            async with session.get(attachment.url) as resp:
                if resp.status != 200:
                    continue
                image_data = await resp.read()

            # Appliquer le watermark
            watermarked = add_watermark(image_data)

            # Préparer le fichier Discord
            filename = f"dlp_{attachment.filename.rsplit('.', 1)[0]}.jpg"
            files_to_send.append(discord.File(io.BytesIO(watermarked), filename=filename))

    if files_to_send:
        await message.reply(
            f"✅ **{len(files_to_send)} photo(s)** avec filigrane DLP WORKS !",
            files=files_to_send
        )


client.run(TOKEN)

import discord
import io
import os
import traceback
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import certifi
import ssl
import asyncio

# ──────────────────────────────────────────
# ⚙️  CONFIGURATION
# ──────────────────────────────────────────
TOKEN = os.getenv("TOKEN")

WATERMARK_TEXT      = "DLP WORKS"
WATERMARK_OPACITY   = 0
WATERMARK_FONT_SIZE = 80

LOGO_FILE           = "logo.png"
LOGO_SIZE_PERCENT   = 11
LOGO_MARGIN         = 20

OUTPUT_QUALITY      = 100

TIMEOUT_SECONDES    = 60    # Délai max pour télécharger une image
MAX_RETRIES         = 3     # Nombre de tentatives si échec réseau
# ──────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

ssl_context = ssl.create_default_context(cafile=certifi.where())


def add_watermark(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = img.size

    # ── WATERMARK TEXTE ──
    txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)

    font = None
    for font_path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "arial.ttf",
    ]:
        try:
            font = ImageFont.truetype(font_path, WATERMARK_FONT_SIZE)
            break
        except:
            continue
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
    txt_w = bbox[2] - bbox[0]
    txt_h = bbox[3] - bbox[1]

    padding = 20
    txt_img = Image.new("RGBA", (txt_w + padding*2, txt_h + padding*2), (255, 255, 255, 0))
    txt_draw = ImageDraw.Draw(txt_img)
    txt_draw.text(
        (padding, padding), WATERMARK_TEXT, font=font,
        fill=(180, 180, 180, int(255 * WATERMARK_OPACITY / 100))
    )

    txt_rotated = txt_img.rotate(30, expand=True)
    tw, th = txt_rotated.size
    for y in range(-th, height + th, th + 60):
        for x in range(-tw, width + tw, tw + 40):
            txt_layer.paste(txt_rotated, (x, y), txt_rotated)

    img = Image.alpha_composite(img, txt_layer)

    # ── LOGO BAS DROITE ──
    if os.path.exists(LOGO_FILE):
        try:
            logo = Image.open(LOGO_FILE).convert("RGBA")
            logo_width = int(width * LOGO_SIZE_PERCENT / 100)
            logo_ratio = logo_width / logo.size[0]
            logo_height = int(logo.size[1] * logo_ratio)
            logo = logo.resize((logo_width, logo_height), Image.LANCZOS)
            pos_x = width - logo_width - LOGO_MARGIN
            pos_y = height - logo_height - LOGO_MARGIN
            img.paste(logo, (pos_x, pos_y), logo)
        except FileNotFoundError:
            print("⚠️ ERREUR LOGO : fichier logo.png introuvable dans le dossier du bot")
        except Exception as e:
            print(f"⚠️ ERREUR LOGO : impossible de traiter le logo → {e}")

# ── EXPORT avec compression automatique si trop lourd ──
    img_rgb = img.convert("RGB")
    
    for qualite in [OUTPUT_QUALITY, 85, 75, 65]:
        output = io.BytesIO()
        img_rgb.save(output, format="JPEG", quality=qualite, subsampling=0, optimize=True)
        taille = output.tell()
        print(f"📦 Taille export : {taille // 1024} Ko (qualité {qualite})")
        
        if taille < 7 * 1024 * 1024:  # Sous 7 Mo → OK pour Discord
            output.seek(0)
            return output.read()
        
        print(f"⚠️ Trop lourd ({taille // 1024 // 1024} Mo), compression accrue...")

    # Si toujours trop lourd → réduction de la résolution
    print("📐 Image encore trop lourde → réduction de la résolution à 50%")
    w, h = img_rgb.size
    img_rgb = img_rgb.resize((w // 2, h // 2), Image.LANCZOS)
    output = io.BytesIO()
    img_rgb.save(output, format="JPEG", quality=75, optimize=True)
    output.seek(0)
    return output.read()
async def telecharger_image(session, url, nom_fichier, tentative=1):
    """Télécharge une image avec retry automatique si connexion lente."""
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDES)
        async with session.get(url, timeout=timeout) as resp:
            if resp.status == 200:
                return await resp.read()
            elif resp.status == 403:
                print(f"❌ ERREUR RÉSEAU [{nom_fichier}] : Accès refusé par Discord (lien expiré) — tentative {tentative}/{MAX_RETRIES}")
            elif resp.status == 404:
                print(f"❌ ERREUR RÉSEAU [{nom_fichier}] : Image introuvable sur les serveurs Discord (404) — tentative {tentative}/{MAX_RETRIES}")
            else:
                print(f"❌ ERREUR RÉSEAU [{nom_fichier}] : Code HTTP inattendu {resp.status} — tentative {tentative}/{MAX_RETRIES}")
            return None

    except asyncio.TimeoutError:
        print(f"⏱️ ERREUR TIMEOUT [{nom_fichier}] : Connexion trop lente (>{TIMEOUT_SECONDES}s) — tentative {tentative}/{MAX_RETRIES}")
        if tentative < MAX_RETRIES:
            print(f"🔄 Nouvelle tentative dans 3 secondes...")
            await asyncio.sleep(3)
            return await telecharger_image(session, url, nom_fichier, tentative + 1)
        else:
            print(f"❌ ABANDON [{nom_fichier}] : {MAX_RETRIES} tentatives échouées (WiFi trop lent ou instable)")
            return None

    except aiohttp.ClientConnectorError as e:
        print(f"❌ ERREUR CONNEXION [{nom_fichier}] : Impossible de contacter Discord → {e} — tentative {tentative}/{MAX_RETRIES}")
        if tentative < MAX_RETRIES:
            await asyncio.sleep(3)
            return await telecharger_image(session, url, nom_fichier, tentative + 1)
        return None

    except aiohttp.ClientSSLError as e:
        print(f"❌ ERREUR SSL [{nom_fichier}] : Certificat de sécurité invalide → {e}")
        return None

    except Exception as e:
        print(f"❌ ERREUR INCONNUE [{nom_fichier}] : {type(e).__name__} → {e} — tentative {tentative}/{MAX_RETRIES}")
        if tentative < MAX_RETRIES:
            await asyncio.sleep(3)
            return await telecharger_image(session, url, nom_fichier, tentative + 1)
        return None


@client.event
async def on_ready():
    print(f"✅ Bot connecté : {client.user}")
    print(f"📡 Serveurs connectés : {[g.name for g in client.guilds]}")


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    print(f"📨 Message de {message.author} ({message.author.id}) | {len(message.attachments)} pièce(s) jointe(s)")

    images = [
        a for a in message.attachments
        if a.content_type and a.content_type.startswith("image/")
    ]

    if not images:
        return

    print(f"🖼️ {len(images)} image(s) de {message.author} — début du traitement...")

    try:
        await message.channel.typing()

        files_to_send = []
        erreurs = []

        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            for attachment in images:
                print(f"⬇️ Téléchargement de {attachment.filename} ({attachment.size // 1024} Ko)...")

                # Vérification taille fichier
                if attachment.size > 25 * 1024 * 1024:
                    msg = f"ERREUR TAILLE [{attachment.filename}] : fichier trop lourd ({attachment.size // 1024 // 1024} Mo) — limite Discord : 25 Mo"
                    print(f"❌ {msg}")
                    erreurs.append(msg)
                    continue

                image_data = await telecharger_image(session, attachment.url, attachment.filename)

                if image_data is None:
                    erreurs.append(f"Échec téléchargement de {attachment.filename} après {MAX_RETRIES} tentatives (connexion instable)")
                    continue

                try:
                    watermarked = add_watermark(image_data)
                    filename = f"dlp_{attachment.filename.rsplit('.', 1)[0]}.jpg"
                    files_to_send.append(discord.File(io.BytesIO(watermarked), filename=filename))
                    print(f"✅ Filigrane appliqué sur {attachment.filename}")

                except Image.UnidentifiedImageError:
                    msg = f"ERREUR FORMAT [{attachment.filename}] : format d'image non reconnu (pas un JPG/PNG/WEBP valide)"
                    print(f"❌ {msg}")
                    erreurs.append(msg)

                except MemoryError:
                    msg = f"ERREUR MÉMOIRE [{attachment.filename}] : image trop grande pour être traitée"
                    print(f"❌ {msg}")
                    erreurs.append(msg)

                except Exception as e:
                    msg = f"ERREUR TRAITEMENT [{attachment.filename}] : {type(e).__name__} → {e}"
                    print(f"❌ {msg}")
                    traceback.print_exc()
                    erreurs.append(msg)

        # ── Réponse finale ──
        if files_to_send and not erreurs:
            await message.reply(
                f"✅ **{len(files_to_send)} photo(s)** avec filigrane DLP WORKS !",
                files=files_to_send
            )
        elif files_to_send and erreurs:
            await message.reply(
                f"✅ **{len(files_to_send)} photo(s)** traitée(s) !\n⚠️ Échec sur : {', '.join(erreurs)}",
                files=files_to_send
            )
        else:
            await message.reply(
                f"⚠️ Aucune photo traitée.\nCause : {chr(10).join(erreurs)}"
            )

        print(f"📤 Réponse envoyée à {message.author} — {len(files_to_send)} photo(s) OK, {len(erreurs)} erreur(s)")

    except discord.Forbidden:
        print(f"❌ ERREUR PERMISSIONS : le bot n'a pas le droit d'envoyer des fichiers dans ce channel pour {message.author}")

    except discord.HTTPException as e:
        print(f"❌ ERREUR DISCORD : impossible d'envoyer la réponse → code {e.status} : {e.text}")
        try:
            await message.reply("⚠️ Erreur lors de l'envoi de la photo. Le fichier est peut-être trop lourd pour Discord.")
        except:
            pass

    except Exception as e:
        print(f"❌ ERREUR GÉNÉRALE inattendue : {type(e).__name__} → {e}")
        traceback.print_exc()
        try:
            await message.reply("⚠️ Erreur inattendue. Réessaie dans quelques secondes !")
        except:
            pass


client.run(TOKEN)

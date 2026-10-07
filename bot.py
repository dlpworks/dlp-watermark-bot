import discord
import io
import os
import traceback
from PIL import Image
import aiohttp
import certifi
import ssl
import asyncio

# ──────────────────────────────────────────
# ⚙️  CONFIGURATION
# ──────────────────────────────────────────
TOKEN = os.getenv("TOKEN")

LOGO_FILE           = "logo.png"
LOGO_SIZE_PERCENT   = 11
LOGO_MARGIN         = 20

OUTPUT_QUALITY      = 100

TIMEOUT_SECONDES    = 60
MAX_RETRIES         = 3
# ──────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

ssl_context = ssl.create_default_context(cafile=certifi.where())


def add_logo(image_bytes: bytes) -> bytes:
    img_original = Image.open(io.BytesIO(image_bytes))

    # Préserver le profil couleur ICC (crucial pour photos HDR iPhone)
    icc_profile = img_original.info.get("icc_profile")
    img = img_original.convert("RGBA")
    width, height = img.size

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
            print("⚠️ ERREUR LOGO : fichier logo.png introuvable")
        except Exception as e:
            print(f"⚠️ ERREUR LOGO : {e}")

    # ── EXPORT avec compression automatique ──
    img_rgb = img.convert("RGB")
    save_kwargs = {"format": "JPEG", "subsampling": 0, "optimize": True}
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
        print("🎨 Profil couleur ICC préservé")

    for qualite in [OUTPUT_QUALITY, 85, 75, 65]:
        output = io.BytesIO()
        img_rgb.save(output, quality=qualite, **save_kwargs)
        taille = output.tell()
        print(f"📦 Taille export : {taille // 1024} Ko (qualité {qualite})")
        if taille < 7 * 1024 * 1024:
            output.seek(0)
            return output.read()
        print(f"⚠️ Trop lourd ({taille // 1024 // 1024} Mo), compression accrue...")

    # Si toujours trop lourd → réduction résolution
    print("📐 Image trop lourde → réduction résolution à 50%")
    w, h = img_rgb.size
    img_rgb = img_rgb.resize((w // 2, h // 2), Image.LANCZOS)
    output = io.BytesIO()
    img_rgb.save(output, quality=75, **save_kwargs)
    output.seek(0)
    return output.read()


async def telecharger_image(session, url, nom_fichier, tentative=1):
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDES)
        async with session.get(url, timeout=timeout) as resp:
            if resp.status == 200:
                return await resp.read()
            elif resp.status == 403:
                print(f"❌ ERREUR RÉSEAU [{nom_fichier}] : Accès refusé par Discord (lien expiré) — tentative {tentative}/{MAX_RETRIES}")
            elif resp.status == 404:
                print(f"❌ ERREUR RÉSEAU [{nom_fichier}] : Image introuvable sur les serveurs Discord — tentative {tentative}/{MAX_RETRIES}")
            else:
                print(f"❌ ERREUR RÉSEAU [{nom_fichier}] : Code HTTP inattendu {resp.status} — tentative {tentative}/{MAX_RETRIES}")
            return None

    except asyncio.TimeoutError:
        print(f"⏱️ ERREUR TIMEOUT [{nom_fichier}] : Connexion trop lente (>{TIMEOUT_SECONDES}s) — tentative {tentative}/{MAX_RETRIES}")
        if tentative < MAX_RETRIES:
            print(f"🔄 Nouvelle tentative dans 3 secondes...")
            await asyncio.sleep(3)
            return await telecharger_image(session, url, nom_fichier, tentative + 1)
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

    EXTENSIONS_IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tiff"}
    images = [
        a for a in message.attachments
        if (a.content_type and a.content_type.startswith("image/"))
        or any(a.filename.lower().endswith(ext) for ext in EXTENSIONS_IMAGES)
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
                    traite = add_logo(image_data)
                    filename = f"dlp_{attachment.filename.rsplit('.', 1)[0]}.jpg"
                    files_to_send.append(discord.File(io.BytesIO(traite), filename=filename))
                    print(f"✅ Logo appliqué sur {attachment.filename}")

                except Image.UnidentifiedImageError:
                    msg = f"ERREUR FORMAT [{attachment.filename}] : format non reconnu (pas un JPG/PNG/WEBP valide)"
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

        if files_to_send and not erreurs:
            await message.reply(
                f"✅ **{len(files_to_send)} photo(s)** avec logo DLP WORKS !",
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
            await message.reply("⚠️ Erreur lors de l'envoi. Le fichier est peut-être trop lourd pour Discord.")
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

"""Seed initial du catalogue de cartes (pop culture multi-univers).

Lance au boot via init_db si la table cards est vide. Le owner peut
ensuite ajouter / modifier via le dashboard.
"""
from __future__ import annotations


# Format : (name, universe, subtitle, rarity, image_url, description)
# Rarites : common / rare / epic / legendary / mythic
INITIAL_CARDS = [
    # ===== STAR WARS =====
    ("Luke Skywalker", "Star Wars", "Trilogie originale", "legendary",
     "https://lumiere-a.akamaihd.net/v1/images/luke-skywalker-main_269abf1d.jpeg",
     "Le dernier des Jedi. Heros de la Rebellion."),
    ("Darth Vader", "Star Wars", "Episodes IV-VI", "mythic",
     "https://lumiere-a.akamaihd.net/v1/images/databank_darthvader_01_169_a2f44f55.jpeg",
     "Seigneur Sith, ancien Jedi Anakin Skywalker."),
    ("Yoda", "Star Wars", "Saga", "legendary",
     "https://lumiere-a.akamaihd.net/v1/images/databank_yoda_01_169_d5d3eaa3.jpeg",
     "Maitre Jedi millenaire."),
    ("The Mandalorian", "Star Wars", "Disney+", "epic",
     "https://lumiere-a.akamaihd.net/v1/images/the-mandalorian_75ef8052.jpeg",
     "Chasseur de primes Mandalorien, Din Djarin."),
    ("Grogu", "Star Wars", "The Mandalorian", "rare",
     "https://lumiere-a.akamaihd.net/v1/images/cd_grogu_f6173336.jpeg",
     "L'enfant. Espece inconnue, sensible a la Force."),
    ("Boba Fett", "Star Wars", "Saga", "epic",
     "https://lumiere-a.akamaihd.net/v1/images/databank_bobafett_01_169_e7d2f6e4.jpeg",
     "Chasseur de primes legendaire."),
    ("Princess Leia", "Star Wars", "Trilogie originale", "epic",
     "https://lumiere-a.akamaihd.net/v1/images/databank_leiaorgana_01_169_b3aa8194.jpeg",
     "Senatrice et leader de la Rebellion."),
    ("Han Solo", "Star Wars", "Trilogie originale", "epic",
     "https://lumiere-a.akamaihd.net/v1/images/databank_hansolo_01_169_be20e4d1.jpeg",
     "Contrebandier du Millennium Falcon."),
    ("Chewbacca", "Star Wars", "Saga", "rare",
     "https://lumiere-a.akamaihd.net/v1/images/databank_chewbacca_01_169_ec05bcb5.jpeg",
     "Wookiee, copilote du Millennium Falcon."),
    ("Obi-Wan Kenobi", "Star Wars", "Prelogie", "legendary",
     "https://lumiere-a.akamaihd.net/v1/images/databank_obiwankenobi_01_169_46ab6b4a.jpeg",
     "Maitre Jedi, mentor de Luke et Anakin."),

    # ===== ANIME (Shonen) =====
    ("Naruto Uzumaki", "Anime", "Naruto", "epic",
     "https://static.wikia.nocookie.net/naruto/images/d/d6/Naruto_Part_I.png",
     "Ninja de Konoha, ambition Hokage."),
    ("Sasuke Uchiha", "Anime", "Naruto", "epic",
     "https://static.wikia.nocookie.net/naruto/images/c/c1/Sasuke_Shippuden.png",
     "Heritier du clan Uchiha. Sharingan."),
    ("Goku", "Anime", "Dragon Ball", "legendary",
     "https://static.wikia.nocookie.net/dragonball/images/5/5b/GokuNamekSaga.png",
     "Saiyan, Super Saiyan, Ultra Instinct."),
    ("Vegeta", "Anime", "Dragon Ball", "epic",
     "https://static.wikia.nocookie.net/dragonball/images/d/dd/VegetaNamekSaga.png",
     "Prince des Saiyans. Rival eternel de Goku."),
    ("Monkey D. Luffy", "Anime", "One Piece", "legendary",
     "https://static.wikia.nocookie.net/onepiece/images/6/6d/Monkey_D._Luffy_Anime_Post_Timeskip_Infobox.png",
     "Futur Roi des Pirates. Gomu Gomu no Mi."),
    ("Roronoa Zoro", "Anime", "One Piece", "epic",
     "https://static.wikia.nocookie.net/onepiece/images/4/41/Roronoa_Zoro_Anime_Post_Timeskip_Infobox.png",
     "Bretteur trois-sabres. Reve de devenir le meilleur."),
    ("Levi Ackerman", "Anime", "Attack on Titan", "legendary",
     "https://static.wikia.nocookie.net/shingekinokyojin/images/a/a3/Levi_Ackerman_(Anime)_character_image.png",
     "Soldat le plus fort de l'humanite."),
    ("Eren Yeager", "Anime", "Attack on Titan", "epic",
     "https://static.wikia.nocookie.net/shingekinokyojin/images/9/9e/Eren_Jaeger_Anime_Character_Image_(Final_Season).png",
     "Titan Assaillant. Determination absolue."),
    ("Tanjiro Kamado", "Anime", "Demon Slayer", "epic",
     "https://static.wikia.nocookie.net/kimetsu-no-yaiba/images/a/ad/Tanjiro_anime_design.png",
     "Pourfendeur de demons, souffle de l'eau."),
    ("Nezuko Kamado", "Anime", "Demon Slayer", "epic",
     "https://static.wikia.nocookie.net/kimetsu-no-yaiba/images/a/ad/Nezuko_anime_design.png",
     "Demone bambou, soeur de Tanjiro."),
    ("Gojo Satoru", "Anime", "Jujutsu Kaisen", "legendary",
     "https://static.wikia.nocookie.net/jujutsu-kaisen/images/0/05/Satoru_Gojo_Anime_Design.png",
     "Le sorcier le plus fort. Six Eyes + Limitless."),
    ("Itadori Yuji", "Anime", "Jujutsu Kaisen", "epic",
     "https://static.wikia.nocookie.net/jujutsu-kaisen/images/5/53/Yuji_Itadori_anime_design.png",
     "Vaisseau de Sukuna."),
    ("Lelouch Lamperouge", "Anime", "Code Geass", "legendary",
     "https://static.wikia.nocookie.net/codegeass/images/6/64/Lelouch_Lamperouge.png",
     "Geass de l'obeissance. Zero."),
    ("Light Yagami", "Anime", "Death Note", "legendary",
     "https://static.wikia.nocookie.net/deathnote/images/9/9f/Light_Yagami_Anime_Design.png",
     "Detenteur du Death Note. Kira."),
    ("Spike Spiegel", "Anime", "Cowboy Bebop", "epic",
     "https://static.wikia.nocookie.net/cowboybebop/images/2/2d/Spike_Spiegel.png",
     "Chasseur de primes Cowboy Bebop."),

    # ===== JEU VIDEO =====
    ("Master Chief", "Jeu Video", "Halo", "epic",
     "https://www.halopedia.org/images/thumb/b/b6/HINF-Master_Chief.png/300px-HINF-Master_Chief.png",
     "Spartan-II. Sauveur de l'humanite."),
    ("Mario", "Jeu Video", "Nintendo", "rare",
     "https://mario.wiki.gallery/images/thumb/4/4d/MarioNSMBUDeluxe.png/300px-MarioNSMBUDeluxe.png",
     "Plombier italien. It's-a me."),
    ("Link", "Jeu Video", "Zelda", "epic",
     "https://www.zeldadungeon.net/wiki/images/0/06/Link-TotK-Render.png",
     "Heros du Royaume d'Hyrule."),
    ("Kratos", "Jeu Video", "God of War", "epic",
     "https://static.wikia.nocookie.net/godofwar/images/5/58/Kratos_GoWR.png",
     "Fantome de Sparte. Dieu de la Guerre."),
    ("Geralt of Rivia", "Jeu Video", "The Witcher", "epic",
     "https://static.wikia.nocookie.net/witcher/images/8/8b/Geralt_TW3.png",
     "Sorceleur. Loup blanc."),
    ("Solid Snake", "Jeu Video", "Metal Gear", "epic",
     "https://static.wikia.nocookie.net/metalgear/images/5/58/Solid_Snake_in_MGS4.png",
     "Soldat legendaire FOXHOUND."),
    ("Lara Croft", "Jeu Video", "Tomb Raider", "epic",
     "https://static.wikia.nocookie.net/tombraider/images/6/64/Lara_Croft_2018.png",
     "Aventuriere archeologue."),
    ("Sonic the Hedgehog", "Jeu Video", "Sega", "rare",
     "https://static.wikia.nocookie.net/sonic/images/b/bd/Sonic_modern_design.png",
     "Hedgehog supersonique."),
    ("Pikachu", "Jeu Video", "Pokemon", "rare",
     "https://archives.bulbagarden.net/media/upload/thumb/4/4a/0025Pikachu.png/300px-0025Pikachu.png",
     "Pokemon souris electrique."),
    ("Cloud Strife", "Jeu Video", "Final Fantasy VII", "epic",
     "https://static.wikia.nocookie.net/finalfantasy/images/9/9f/Cloud_FFVII_Remake.png",
     "Ex-SOLDAT. Buster Sword."),

    # ===== HAZBIN HOTEL / HELLUVA BOSS =====
    ("Charlie Morningstar", "Hazbin Hotel", "Vivziepop", "epic",
     "https://static.wikia.nocookie.net/hazbinhotel/images/0/0d/Charlie_Morningstar.png",
     "Princesse de l'Enfer. Optimiste."),
    ("Alastor", "Hazbin Hotel", "Vivziepop", "legendary",
     "https://static.wikia.nocookie.net/hazbinhotel/images/3/36/Alastor_Profile.png",
     "Radio Demon. Souriant."),
    ("Vaggie", "Hazbin Hotel", "Vivziepop", "rare",
     "https://static.wikia.nocookie.net/hazbinhotel/images/9/9c/Vaggie_Profile.png",
     "Ange dechu. Garde du corps de Charlie."),
    ("Angel Dust", "Hazbin Hotel", "Vivziepop", "epic",
     "https://static.wikia.nocookie.net/hazbinhotel/images/3/3d/Angel_Dust_Profile.png",
     "Demon araignee. Star de l'industrie..."),
    ("Blitzo", "Helluva Boss", "Vivziepop", "epic",
     "https://static.wikia.nocookie.net/helluva-boss/images/3/3d/Blitzo_2022.png",
     "PDG de I.M.P. Imp anti-heros."),

    # ===== THE AMAZING DIGITAL CIRCUS =====
    ("Pomni", "Digital Circus", "Glitch Productions", "epic",
     "https://static.wikia.nocookie.net/the-amazing-digital-circus/images/c/cc/Pomni.png",
     "Pri sonniere du Cirque Numerique."),
    ("Caine", "Digital Circus", "Glitch Productions", "legendary",
     "https://static.wikia.nocookie.net/the-amazing-digital-circus/images/9/9e/Caine.png",
     "Ringmaster numerique."),
    ("Ragatha", "Digital Circus", "Glitch Productions", "rare",
     "https://static.wikia.nocookie.net/the-amazing-digital-circus/images/4/4f/Ragatha.png",
     "Poupee de chiffon optimiste."),
    ("Jax", "Digital Circus", "Glitch Productions", "rare",
     "https://static.wikia.nocookie.net/the-amazing-digital-circus/images/4/47/Jax.png",
     "Lapin sarcastique."),

    # ===== Pop culture divers (common) =====
    ("Stan Marsh", "Pop Culture", "South Park", "common",
     "https://static.wikia.nocookie.net/southpark/images/d/db/Stan_Marsh.png",
     "Resident de South Park."),
    ("Homer Simpson", "Pop Culture", "The Simpsons", "common",
     "https://static.wikia.nocookie.net/simpsons/images/0/02/Homer_Simpson_2006.png",
     "D'oh!"),
    ("SpongeBob", "Pop Culture", "Nickelodeon", "rare",
     "https://static.wikia.nocookie.net/spongebob/images/3/3b/SpongeBob_stock_art.png",
     "Eponge de mer cuistot."),
    ("Rick Sanchez", "Pop Culture", "Rick and Morty", "epic",
     "https://static.wikia.nocookie.net/rickandmorty/images/a/a6/Rick_Sanchez.png",
     "Genie alcoolique multidimensionnel."),
    ("Walter White", "Pop Culture", "Breaking Bad", "epic",
     "https://static.wikia.nocookie.net/breakingbad/images/f/fc/WalterWhite.png",
     "I am the one who knocks."),
    ("Shrek", "Pop Culture", "DreamWorks", "rare",
     "https://static.wikia.nocookie.net/shrek/images/5/57/Shrek_in_Shrek_Forever_After.png",
     "Ogre du marais."),
]


def seed_initial_cards():
    """Insere les cartes initiales SI la table est vide.
    Idempotent : a appeler au boot. Les image_url Wikia ne marchent pas
    bien avec Discord, on insere avec image_url=None et on attend le
    refresh-images (Wikipedia API) via dashboard owner."""
    from database import card_count_total, card_add
    if card_count_total() > 0:
        return 0
    n = 0
    for name, universe, subtitle, rarity, _img, description in INITIAL_CARDS:
        try:
            card_add(name, universe, subtitle, rarity, None, description)
            n += 1
        except Exception as e:
            print(f"[cards seed] err {name}: {e}")
    print(f"[cards seed] {n} cartes initiales inserees (sans image, run refresh-images)")
    return n

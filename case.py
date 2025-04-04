import random
import requests
from bs4 import BeautifulSoup
import urllib.parse
import webbrowser
import time

# ======== CONFIG ========

case_contents = {
    "Mil-Spec (Blue)": [
        "MAC-10 | Oceanic (Field-Tested)",
        "CZ75-Auto | Tacticat (Field-Tested)",
        "UMP-45 | Exposure (Field-Tested)"
    ],
    "Restricted (Purple)": [
        "AK-47 | The Empress (Field-Tested)",
        "Glock-18 | Off World (Field-Tested)"
    ],
    "Classified (Pink)": [
        "P250 | See Ya Later (Field-Tested)"
    ],
    "Covert (Red)": [
        "M4A1-S | Decimator (Field-Tested)"
    ],
    "Rare Special Item (Gold)": [
        "★ Karambit | Lore (Field-Tested)"
    ]
}

rarity_weights = {
    "Mil-Spec (Blue)": 79.92,
    "Restricted (Purple)": 15.98,
    "Classified (Pink)": 3.2,
    "Covert (Red)": 0.64,
    "Rare Special Item (Gold)": 0.26
}

# ======== FUNCTIONS ========

def weighted_random_choice(weighted_dict):
    items = list(weighted_dict.keys())
    weights = list(weighted_dict.values())
    return random.choices(items, weights=weights, k=1)[0]

def get_skin_price(skin_name):
    url = "https://steamcommunity.com/market/priceoverview/"
    params = {
        "currency": 2,  # GBP
        "appid": 730,
        "market_hash_name": skin_name
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Yes I'm back again, Steam)"
    }

    response = requests.get(url, params=params, headers=headers)
    if response.status_code != 200:
        print(f"Steam rejected your humble request: {response.status_code}")
        return None

    data = response.json()
    if not data.get("success"):
        print("Steam API didn’t like that skin. Much like the matchmaker.")
        return None

    return data.get("lowest_price", "¯\\_(ツ)_/¯ No price listed")

def get_skin_image_url(skin_name):
    base_url = "https://steamcommunity.com/market/listings/730/"
    skin_url = base_url + urllib.parse.quote(skin_name)

    headers = {
        "User-Agent": "Mozilla/5.0 (Totally a human)"
    }

    response = requests.get(skin_url, headers=headers)
    if response.status_code != 200:
        print("Steam's image server rolled a critical fail.")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    img_tag = soup.find("img", class_="market_listing_item_img")

    if img_tag and img_tag.get("src"):
        return img_tag["src"]
    else:
        print("Image not found. Your skin is invisible.")
        return None

def open_case():
    print("\n📦 Opening case... please hold your misplaced excitement.\n")
    rarity = weighted_random_choice(rarity_weights)
    skin = random.choice(case_contents[rarity])
    print(f"You unboxed: {skin} [{rarity}]")

    print("\n💰 Fetching market price...")
    price = get_skin_price(skin)
    if price:
        print(f"💷 Current market price: {price}")
    else:
        print("💸 Price unknown. Spiritually: priceless. Financially: worthless.")

    print("\n🖼️ Loading image...")
    img_url = get_skin_image_url(skin)
    if img_url:
        print(f"Image URL: {img_url}")
        webbrowser.open(img_url)
    else:
        print("Could not load image. Use your imagination.")

# ======== RUN ========
if __name__ == "__main__":
    open_case()

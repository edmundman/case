# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
from discord import app_commands
from discord import ui # Import UI elements for buttons
import asyncio
import os
import random
import requests
from bs4 import BeautifulSoup
import urllib.parse
import io
import json
import re
from typing import Optional, List # For optional command arguments and type hinting

# Load Opus library if needed for other voice features (though core VC is removed)
# Consider removing if absolutely no voice planned.
try:
    discord.opus.load_opus('/usr/lib/libopus.so.0') # Adjust path if necessary
    if not discord.opus.is_loaded():
        print("Opus failed to load, but trying default.")
        discord.opus._load_default()
except Exception as e:
    print(f"Could not load opus library: {e}. Trying default.")
    try:
        discord.opus._load_default()
    except Exception as e_def:
         print(f"Could not load default opus: {e_def}. Voice sending might fail if ever re-added.")


intents = discord.Intents.default() # Start with default intents
intents.message_content = True     # Need message content for prefix commands
intents.members = True             # Need members intent for fetching user info for leaderboard/inventory
intents.reactions = True           # Might be useful for interactions

# Consider if you need guilds intent depending on server-specific features
# intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- Configuration ---
# !! WARNING: Enabling ban on knife is generally NOT recommended! !!
ENABLE_BAN_ON_KNIFE = True # Set to True to enable banning users who unbox a knife

# --- User Data System (Inventory, Profit/Loss, Cases Opened) ---
USER_DATA_FILE = "user_data.json"
# Structure: { user_id: {"inventory": {item_name: count}, "profit_loss": float, "cases_opened": int} }
user_data = {}

def load_user_data():
    """Loads user data from the JSON file."""
    global user_data
    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, 'r', encoding='utf-8') as f: # Specify encoding
                loaded_data = json.load(f)
                user_data = {}
                for k, v in loaded_data.items():
                    try:
                        user_id = int(k)
                        inventory = v.get("inventory", {})
                        profit_loss = float(v.get("profit_loss", 0.0))
                        cases_opened = int(v.get("cases_opened", 0)) # Load cases opened
                        user_data[user_id] = {
                            "inventory": inventory,
                            "profit_loss": profit_loss,
                            "cases_opened": cases_opened
                        }
                    except (ValueError, TypeError) as e:
                        print(f"Skipping invalid data entry for key {k}: {e}")
                print("User data loaded.")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error loading user data file: {e}. Starting with empty data.")
            user_data = {}
        except Exception as e:
             print(f"An unexpected error occurred loading user data: {e}")
             user_data = {}
    else:
        print("User data file not found. Starting with empty data.")
        user_data = {}

def save_user_data():
    """Saves the current user data to the JSON file."""
    global user_data
    try:
        # Create a copy to avoid issues during iteration if data changes
        data_to_save = {str(k): v for k, v in user_data.items()}
        with open(USER_DATA_FILE, 'w', encoding='utf-8') as f: # Specify encoding
            json.dump(data_to_save, f, indent=4)
    except Exception as e:
        print(f"Error saving user data file: {e}")

def get_user_data_entry(user_id: int):
    """Gets the data entry for a user, initializing if needed."""
    global user_data
    if user_id not in user_data:
        user_data[user_id] = {"inventory": {}, "profit_loss": 0.0, "cases_opened": 0}
    # Ensure existing users also have the cases_opened key
    elif "cases_opened" not in user_data[user_id]:
         user_data[user_id]["cases_opened"] = 0
    return user_data[user_id]

def update_user_score(user_id: int, amount: float):
    """Adds/subtracts an amount from the user's profit_loss score."""
    user_entry = get_user_data_entry(user_id)
    user_entry["profit_loss"] = user_entry.get("profit_loss", 0.0) + amount
    # Saving happens after all updates in the command usually

def add_item_to_user_inventory(user_id: int, item_name: str):
    """Adds an item to a user's inventory."""
    user_entry = get_user_data_entry(user_id)
    inventory = user_entry.get("inventory", {})
    inventory[item_name] = inventory.get(item_name, 0) + 1
    user_entry["inventory"] = inventory
    # Saving happens after all updates

def increment_cases_opened(user_id: int):
    """Increments the cases opened counter for a user."""
    user_entry = get_user_data_entry(user_id)
    user_entry["cases_opened"] = user_entry.get("cases_opened", 0) + 1
    # Saving happens after all updates

def parse_price(price_str: str) -> float:
    """Parses a price string (e.g., '£1,234.56', '$5.99', '12,34€') into a float."""
    if not price_str or not isinstance(price_str, str):
        return 0.0
    # Remove currency symbols, thousands separators (except the last comma/dot)
    cleaned_str = re.sub(r'[^\d,.]', '', price_str)
    # Handle potential multiple separators (e.g., 1,234.56 or 1.234,56)
    parts = re.split(r'[,\.]', cleaned_str)
    if len(parts) > 1:
        # Assume the last part is decimal, join the rest
        num_str = "".join(parts[:-1]) + "." + parts[-1]
    else:
        num_str = parts[0]

    try:
        return float(num_str)
    except ValueError:
        # Fallback for simple numbers if complex parsing fails
        try:
            return float(cleaned_str.replace(',', '.')) # Try replacing comma just in case
        except ValueError:
            print(f"Could not parse price string: {price_str} -> {cleaned_str}")
            return 0.0


# --- Load initial data ---
load_user_data()

# --- CS:GO Case & Item Data ---

# Define conditions and their approximate chances
# These chances are illustrative - adjust them as you see fit!
condition_chances = {
    " (Factory New)": 10.0,       # 10% chance
    " (Minimal Wear)": 25.0,      # 25% chance
    " (Field-Tested)": 40.0,      # 40% chance (Most common)
    " (Well-Worn)": 15.0,         # 15% chance
    " (Battle-Scarred)": 10.0,    # 10% chance
}
# Ensure chances sum close to 100 or normalize later if needed

# Define Cases
# NOTE: Costs are fixed examples. Contents are BASE skin names.
# Replace with accurate data from reliable sources.
all_cases = {
    "Original Mix Case": {
        "cost": 2.50, # Example fixed cost (Case + Key approx)
        "contents": {
            "Mil-Spec (Blue)": ["MAC-10 | Oceanic", "CZ75-Auto | Tacticat", "UMP-45 | Exposure"],
            "Restricted (Purple)": ["AK-47 | The Empress", "Glock-18 | Off World"],
            "Classified (Pink)": ["P250 | See Ya Later"],
            "Covert (Red)": ["M4A1-S | Decimator"],
            "Rare Special Item (Gold)": ["★ Karambit | Lore"] # Base knife name
        },
        "weights": { # Standard CS:GO odds
            "Mil-Spec (Blue)": 79.92327, "Restricted (Purple)": 15.98465, "Classified (Pink)": 3.19693,
            "Covert (Red)": 0.63939, "Rare Special Item (Gold)": 0.25576
        }
    },
    "Revolution Case": {
        "cost": 1.50, # Example cost
        "contents": { # EXAMPLE CONTENTS - REPLACE WITH REAL DATA
            "Mil-Spec (Blue)": ["MP9 | Featherweight", "P250 | Re.built", "MAG-7 | Insomnia"],
            "Restricted (Purple)": ["Glock-18 | Umbral Rabbit", "MAC-10 | Sakkaku"],
            "Classified (Pink)": ["R8 Revolver | Banana Cannon", "P90 | Neoqueen"],
            "Covert (Red)": ["AK-47 | Head Shot", "M4A4 | Temukau"],
            "Rare Special Item (Gold)": ["★ Specialist Gloves | Kimono"] # Base glove name
        },
        "weights": { # Standard CS:GO odds
            "Mil-Spec (Blue)": 79.92327, "Restricted (Purple)": 15.98465, "Classified (Pink)": 3.19693,
            "Covert (Red)": 0.63939, "Rare Special Item (Gold)": 0.25576
        }
    },
    "Dreams & Nightmares Case": {
        "cost": 0.80, # Example cost
        "contents": { # EXAMPLE CONTENTS - REPLACE WITH REAL DATA
            "Mil-Spec (Blue)": ["Five-SeveN | Scrawl", "SCAR-20 | Poultrygeist", "Sawed-Off | Spirit Board"],
            "Restricted (Purple)": ["MP7 | Abyssal Apparition", "XM1014 | Zombie Offensive", "Dual Berettas | Melondrama"],
            "Classified (Pink)": ["USP-S | Ticket to Hell", "G3SG1 | Dream Glade", "FAMAS | Rapid Eye Movement"],
            "Covert (Red)": ["AK-47 | Nightwish", "MP9 | Starlight Protector"],
            "Rare Special Item (Gold)": ["★ Butterfly Knife | Gamma Doppler", "★ Huntsman Knife | Lore", "★ Bowie Knife | Autotronic"] # Example multiple knives/gloves
        },
        "weights": { # Standard CS:GO odds
             "Mil-Spec (Blue)": 79.92327, "Restricted (Purple)": 15.98465, "Classified (Pink)": 3.19693,
            "Covert (Red)": 0.63939, "Rare Special Item (Gold)": 0.25576
        }
    },
     "Kilowatt Case": { # EXAMPLE - NEEDS REAL DATA
        "cost": 4.00,
        "contents": {
            "Mil-Spec (Blue)": ["Tec-9 | Slag", "UMP-45 | Motorized", "Dual Berettas | Hideout"],
            "Restricted (Purple)": ["Five-SeveN | Hybrid", "MAC-10 | Light Box", "SSG 08 | Dezastre"],
            "Classified (Pink)": ["Sawed-Off | Analog Input", "USP-S | Jawbreaker", "Zeus x27 | Olympus"],
            "Covert (Red)": ["AK-47 | Inheritance", "M4A1-S | Black Lotus"],
            "Rare Special Item (Gold)": ["★ Kukri Knife | Fade", "★ Kukri Knife | Slaughter", "★ Kukri Knife | Case Hardened"] # Example new knife
        },
        "weights": { # Standard CS:GO odds
             "Mil-Spec (Blue)": 79.92327, "Restricted (Purple)": 15.98465, "Classified (Pink)": 3.19693,
            "Covert (Red)": 0.63939, "Rare Special Item (Gold)": 0.25576
        }
    },
    "Clutch Case": { # EXAMPLE - NEEDS REAL DATA
        "cost": 0.50,
        "contents": {
            "Mil-Spec (Blue)": ["MP9 | Black Sand", "Five-SeveN | Flame Test", "P2000 | Urban Hazard"],
            "Restricted (Purple)": ["SG 553 | Aloha", "XM1014 | Oxide Blaze", "Glock-18 | Moonrise"],
            "Classified (Pink)": ["AWP | Mortis", "UMP-45 | Arctic Wolf", "AUG | Stymphalian"],
            "Covert (Red)": ["M4A4 | Neo-Noir", "USP-S | Cortex"],
            "Rare Special Item (Gold)": ["★ Hydra Gloves | Emerald", "★ Sport Gloves | Vice", "★ Driver Gloves | King Snake"] # Example gloves
        },
        "weights": { # Standard CS:GO odds
             "Mil-Spec (Blue)": 79.92327, "Restricted (Purple)": 15.98465, "Classified (Pink)": 3.19693,
            "Covert (Red)": 0.63939, "Rare Special Item (Gold)": 0.25576
        }
    }
    # Add more cases here following the same structure
    # Make sure 'contents' use BASE skin names
}

# --- Helper Functions ---
def weighted_random_choice(weighted_dict):
    """Selects a key from a dictionary based on its value (weight)."""
    total_weight = sum(weighted_dict.values())
    if total_weight <= 0:
        # Fallback if weights are invalid, maybe return a random key or None
        if not weighted_dict: return None
        print("Warning: Invalid weights in weighted_random_choice, falling back to random.")
        return random.choice(list(weighted_dict.keys()))

    random_num = random.uniform(0, total_weight)
    current_weight = 0
    for item, weight in weighted_dict.items():
        current_weight += weight
        if random_num <= current_weight:
            return item
    # Should not be reached if total_weight > 0, but as a fallback:
    return random.choice(list(weighted_dict.keys())) if weighted_dict else None

async def get_steam_market_data(item_name: str, session: requests.Session) -> Optional[dict]:
    """Fetches price overview data from Steam Market asynchronously using a session."""
    url = "https://steamcommunity.com/market/priceoverview/"
    params = {"currency": 2, "appid": 730, "market_hash_name": item_name } # Currency 2 = GBP (£)
    headers = {"User-Agent": f"DiscordBot/1.0 (Market Check for {item_name})"} # More specific UA
    try:
        # Use asyncio.to_thread for blocking requests.get within the async function
        response = await asyncio.to_thread(session.get, url, params=params, headers=headers, timeout=10) # Add timeout

        if response.status_code == 429:
             print(f"Rate limited by Steam API for {item_name}. Waiting...")
             await asyncio.sleep(random.uniform(5, 15)) # Wait before potential retry (if implemented)
             return None # Indicate rate limit
        elif response.status_code != 200:
            print(f"Steam Price API Error {response.status_code} for {item_name}. Response: {response.text[:200]}") # Log snippet
            return None

        data = response.json()
        if not data:
             print(f"Steam Price API returned empty data for {item_name}")
             return None
        if not data.get("success"):
            # Don't flood console for items not on market, but log if needed for debugging
            # print(f"Steam Price API reported failure for {item_name}: {data}")
            return None
        return data
    except requests.exceptions.Timeout:
         print(f"Timeout getting Steam price for {item_name}")
         return None
    except requests.exceptions.RequestException as e:
        print(f"Network error getting Steam price for {item_name}: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON decode error for Steam price ({item_name}): {e}. Response text: {response.text[:200]}")
        return None
    except Exception as e:
        print(f"Unexpected error in get_steam_market_data for {item_name}: {e}")
        return None


async def get_skin_price_str(item_name: str, session: requests.Session) -> Optional[str]:
    """Gets the 'lowest_price' or 'median_price' string from Steam API."""
    data = await get_steam_market_data(item_name, session)
    if data:
        # Prefer lowest_price, fall back to median_price if lowest is missing
        return data.get("lowest_price") or data.get("median_price")
    return None


async def get_skin_image_url(skin_name: str, session: requests.Session):
    """Gets the market listing image URL for a skin using a session."""
    base_url = "https://steamcommunity.com/market/listings/730/"
    # Ensure the skin name is URL encoded
    skin_url = base_url + urllib.parse.quote(skin_name)
    headers = {"User-Agent": f"DiscordBot/1.0 (Market Image Check for {skin_name})"}
    try:
        # Use asyncio.to_thread for blocking requests.get within the async function
        response = await asyncio.to_thread(session.get, skin_url, headers=headers, timeout=10) # Add timeout

        if response.status_code == 429:
             print(f"Rate limited by Steam getting image for {skin_name}. Waiting...")
             await asyncio.sleep(random.uniform(5, 15))
             return None # Indicate rate limit
        elif response.status_code != 200:
            # Don't spam for 404s, but log other errors
            if response.status_code != 404:
                print(f"Steam Market Error {response.status_code} getting image page for {skin_name}")
            return None

        # Use BeautifulSoup to parse the HTML
        soup = await asyncio.to_thread(BeautifulSoup, response.text, 'html.parser')

        # Find the large image element
        img_div = soup.find("div", class_="market_listing_largeimage")
        if img_div:
             img_tag = img_div.find("img", id="mainContentsContainer_item_image")
             if img_tag and img_tag.get("src"):
                src = img_tag["src"]
                # Sometimes the src is relative, sometimes absolute
                if src.startswith("https://steamcommunity-a.akamaihd.net/"):
                    return src
                elif not src.startswith("http"):
                     # Fallback if structure changes, try constructing absolute URL
                     # This might need adjustment if Steam changes CDN path
                     return "https://steamcommunity-a.akamaihd.net/economy/image/" + src
                else:
                    return src # Already absolute URL

        # Fallback: try finding the smaller image often used in listings
        img_tag_small = soup.find("img", class_="market_listing_item_img")
        if img_tag_small and img_tag_small.get("src"):
            src = img_tag_small["src"]
            if src.startswith("https://steamcommunity-a.akamaihd.net/"):
                return src
            elif not src.startswith("http"):
                 return "https://steamcommunity-a.akamaihd.net/economy/image/" + src
            else:
                return src

        # print(f"Could not find image tag for {skin_name} on page {skin_url}")
        return None
    except requests.exceptions.Timeout:
         print(f"Timeout getting Steam image for {skin_name}")
         return None
    except requests.exceptions.RequestException as e:
        print(f"Network error getting Steam image for {skin_name}: {e}")
        return None
    except Exception as e:
        # Catch potential BeautifulSoup errors or others
        print(f"Error parsing image page or getting image for {skin_name}: {e}")
        return None


# --- UI Views ---

class InventoryView(discord.ui.View):
    """Adds a recalculate button to the inventory message."""
    def __init__(self, original_user_id: int, timeout=180): # Timeout after 3 minutes
        super().__init__(timeout=timeout)
        self.original_user_id = original_user_id
        self.recalculate_button = discord.ui.Button(label="Recalculate Current Value", style=discord.ButtonStyle.primary, custom_id="recalc_inv_value")
        self.recalculate_button.callback = self.recalculate_callback # Assign callback here
        self.add_item(self.recalculate_button)
        self.message = None # To store the message this view is attached to

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the original command user to interact
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Sorry, only the person who requested the inventory can recalculate.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        # Disable button on timeout
        self.recalculate_button.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass # Message might have been deleted
            except discord.HTTPException as e:
                 print(f"Error disabling button on timeout: {e}")

    async def recalculate_callback(self, interaction: discord.Interaction):
        """Callback for the recalculate button."""
        await interaction.response.defer(thinking=True, ephemeral=False) # Show loading state

        user_id = interaction.user.id
        user_entry = get_user_data_entry(user_id)
        user_inv = user_entry.get("inventory", {})

        if not user_inv:
            await interaction.followup.send("Your inventory is empty, nothing to recalculate.", ephemeral=True)
            return

        total_recalculated_value = 0.0
        items_processed = 0
        items_failed = 0
        rate_limit_waits = 0
        max_retries = 2 # Max retries per item on rate limit

        # Use a single session for all requests in this batch
        async with asyncio.timeout(120): # Timeout for the whole recalc process (e.g., 2 mins)
            try:
                async with aiohttp.ClientSession() as session: # Using aiohttp for better async handling
                    tasks = []
                    item_counts = {} # Store counts to multiply later

                    for item_name, count in user_inv.items():
                         tasks.append(self.fetch_item_price(item_name, session))
                         item_counts[item_name] = count

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for item_name, result in zip(item_counts.keys(), results):
                        if isinstance(result, Exception):
                             print(f"Error fetching price during recalc for {item_name}: {result}")
                             items_failed += 1
                        elif result is None:
                             items_failed += 1 # Price not found or API error
                        else:
                            item_value = parse_price(result) # result is price_str here
                            total_recalculated_value += (item_value * item_counts[item_name])
                            items_processed += 1

            except asyncio.TimeoutError:
                 await interaction.followup.send("Recalculation timed out. Please try again later.", ephemeral=True)
                 return
            except Exception as e:
                print(f"Unexpected error during inventory recalculation: {e}")
                await interaction.followup.send(f"An unexpected error occurred during recalculation: {e}", ephemeral=True)
                return


        # Disable button after successful calculation
        self.recalculate_button.disabled = True
        # Update the original message's view
        try:
            if self.message: await self.message.edit(view=self)
        except discord.NotFound: pass # Ignore if original message deleted
        except discord.HTTPException as e: print(f"Error disabling button after recalc: {e}")


        result_embed = discord.Embed(
            title=f"{interaction.user.display_name}'s Recalculated Inventory Value",
            description=f"Estimated current market value of your inventory: **£{total_recalculated_value:.2f}**",
            color=discord.Color.blue()
        )
        result_embed.set_footer(text=f"Processed {items_processed} item types. Failed to fetch price for {items_failed} types.")

        await interaction.followup.send(embed=result_embed) # Send result as a followup

    async def fetch_item_price(self, item_name: str, session):
        """Helper to fetch price, potentially move outside if used elsewhere"""
        # Simplified version for recalc - does not need full market data, just price str
        # Note: This uses requests via to_thread, consider switching to aiohttp if performance is critical
        # For simplicity, sticking with requests for now.
        temp_req_session = requests.Session() # Create session within the async task
        price_str = await get_skin_price_str(item_name, temp_req_session)
        temp_req_session.close() # Close session
        return price_str


# --- Cog for Case and General Commands ---
class CaseCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Use a persistent session for Steam API calls to potentially reuse connections
        self.http_session = requests.Session()
        # Rate limiting simple state
        self.last_api_call_time = 0
        self.api_call_delay = 1.5 # Seconds between calls (adjust as needed)


    def cog_unload(self):
        """Close the session when the cog is unloaded."""
        self.http_session.close()
        print("HTTP session closed for CaseCommands.")


    async def check_api_rate_limit(self):
         """Simple delay-based rate limiting for Steam API calls."""
         now = asyncio.get_event_loop().time()
         time_since_last_call = now - self.last_api_call_time
         if time_since_last_call < self.api_call_delay:
             await asyncio.sleep(self.api_call_delay - time_since_last_call)
         self.last_api_call_time = asyncio.get_event_loop().time()


    @commands.command(name="cases")
    async def list_cases(self, ctx):
        """Lists the available cases and their opening costs."""
        embed = discord.Embed(title="Available Cases", color=discord.Color.orange())
        description = ""
        for name, data in all_cases.items():
            cost = data.get('cost', 'N/A')
            cost_str = f"£{cost:.2f}" if isinstance(cost, (int, float)) else str(cost)
            description += f"🔹 **{name}** - Cost: {cost_str}\n"

        if not description:
            description = "No cases configured."

        embed.description = description
        await ctx.send(embed=embed)

    @commands.command(name="case")
    async def case_command(self, ctx, *, case_name_input: Optional[str] = None):
        """Opens a CS:GO case. Specify name or leave blank for random."""
        user_id = ctx.author.id
        member = ctx.author # Get member object for potential ban

        chosen_case_data = None
        chosen_case_name = None

        if not case_name_input:
            # Randomly select a case if none provided
            if not all_cases:
                 await ctx.send("No cases are configured!")
                 return
            chosen_case_name = random.choice(list(all_cases.keys()))
            chosen_case_data = all_cases[chosen_case_name]
            await ctx.send(f"Randomly selected: **{chosen_case_name}**")
        else:
            # Find the chosen case (case-insensitive matching)
            found = False
            for name, data in all_cases.items():
                if case_name_input.lower() == name.lower():
                    chosen_case_data = data
                    chosen_case_name = name
                    found = True
                    break
            if not found:
                # Simple fuzzy matching: check if input is substring of any case name
                possible_matches = [name for name in all_cases if case_name_input.lower() in name.lower()]
                if len(possible_matches) == 1:
                     chosen_case_name = possible_matches[0]
                     chosen_case_data = all_cases[chosen_case_name]
                     await ctx.send(f"Assuming you meant: **{chosen_case_name}**")
                elif len(possible_matches) > 1:
                     await ctx.send(f"Found multiple possible matches for '{case_name_input}'. Please be more specific: `{'`, `'.join(possible_matches)}`")
                     return
                else:
                    await ctx.send(f"Sorry, I couldn't find the case '{case_name_input}'. Use `!cases` to see available ones.")
                    return

        # --- Get Case Cost ---
        case_cost = chosen_case_data.get('cost', 0.0)
        if not isinstance(case_cost, (int, float)) or case_cost <= 0:
            await ctx.send(f"Error: The cost for '{chosen_case_name}' is not configured correctly.")
            return

        # --- Increment cases opened and Deduct cost ---
        increment_cases_opened(user_id)
        update_user_score(user_id, -case_cost)
        save_user_data() # Save after score/count updates
        # ---

        embed = discord.Embed(title=f"📦 Opening {chosen_case_name}...",
                              description=f"Opening cost: **£{case_cost:.2f}**\nSpinning the wheel...",
                              color=discord.Color.blue())
        message = await ctx.send(embed=embed)

        await asyncio.sleep(0.75) # Slightly longer pause

        # --- Determine Rarity, Base Skin, and Condition ---
        case_weights = chosen_case_data.get("weights")
        case_contents = chosen_case_data.get("contents")

        if not case_weights or not case_contents:
            await message.edit(embed=discord.Embed(title="Error", description=f"Configuration error for '{chosen_case_name}'. Missing weights or contents.", color=discord.Color.red()))
            # Consider refunding cost if config is broken? For now, score remains deducted.
            return

        # 1. Determine Rarity
        rarity = weighted_random_choice(case_weights)
        if not rarity or rarity not in case_contents or not case_contents[rarity]:
            await message.edit(embed=discord.Embed(title="Error", description=f"Configuration error for '{chosen_case_name}'. Could not determine item pool for rarity '{rarity}'.", color=discord.Color.red()))
            return

        # 2. Determine Base Skin from that Rarity
        base_skin = random.choice(case_contents[rarity])

        # 3. Determine Condition (Wear)
        condition_suffix = weighted_random_choice(condition_chances)
        if not condition_suffix:
            print("Warning: Could not determine condition, defaulting to Field-Tested.")
            condition_suffix = " (Field-Tested)" # Fallback

        # 4. Combine to final skin name
        skin = f"{base_skin}{condition_suffix}"
        # ---

        # --- !! BAN LOGIC !! ---
        if rarity == "Rare Special Item (Gold)" and ENABLE_BAN_ON_KNIFE:
            try:
                ban_embed = discord.Embed(title="🚨 RARE ITEM UNBOXED! 🚨", description=f"{member.mention} unboxed **{skin}** ({rarity}) from {chosen_case_name}! Initiating protocol...", color=discord.Color.gold())
                # Fetch image for the ban message if possible
                await self.check_api_rate_limit()
                ban_img_url = await get_skin_image_url(skin, self.http_session)
                if ban_img_url: ban_embed.set_thumbnail(url=ban_img_url)

                await message.edit(embed=ban_embed)
                await asyncio.sleep(2.5) # More dramatic pause

                await member.ban(reason=f"Unboxed a rare item ({skin}) from {chosen_case_name}!")
                await ctx.send(f"*{member.display_name} has been banned for unboxing a rare item.* Good luck!")
                print(f"Banned {member.name} ({member.id}) for unboxing {skin}.")
                # Stop further processing for this command if banned
                return
            except discord.Forbidden:
                 await ctx.send(f"⚠️ {member.mention} unboxed **{skin}**! I tried to ban them, but I lack the 'Ban Members' permission.")
            except discord.HTTPException as e:
                 await ctx.send(f"⚠️ {member.mention} unboxed **{skin}**! Failed to ban due to an API error: {e}")
            except Exception as e:
                await ctx.send(f"⚠️ {member.mention} unboxed **{skin}**! An unexpected error occurred during the ban process: {e}")
            # Continue processing even if ban failed (show item, update score etc.)
        # --- !! END BAN LOGIC !! ---

        # --- Add item to inventory ---
        add_item_to_user_inventory(user_id, skin)
        # ---

        # --- Get Price and Image ---
        await self.check_api_rate_limit()
        price_str_task = asyncio.create_task(get_skin_price_str(skin, self.http_session))
        await self.check_api_rate_limit() # Separate small delay before image fetch too
        img_url_task = asyncio.create_task(get_skin_image_url(skin, self.http_session))

        price_str = await price_str_task
        img_url = await img_url_task
        # ---

        # --- Calculate Value and Update Score ---
        item_value = parse_price(price_str) if price_str else 0.0
        if item_value > 0:
            update_user_score(user_id, item_value)
        # ---
        save_user_data() # Save data after item add and potential score update

        # --- Prepare Result Embed ---
        color_map = {
            "Mil-Spec (Blue)": discord.Color.blue(), "Restricted (Purple)": discord.Color.purple(),
            "Classified (Pink)": discord.Color.magenta(), "Covert (Red)": discord.Color.red(),
            "Rare Special Item (Gold)": discord.Color.gold()
        }
        current_profit_loss = user_data.get(user_id, {}).get("profit_loss", 0.0)
        cases_opened_total = user_data.get(user_id, {}).get("cases_opened", 0)

        result_description = (
            f"From: **{chosen_case_name}**\n"
            f"Rarity: **{rarity}**\n"
            f"Market Value: **{price_str or 'Unknown'}** (Profit/Loss from this item: £{item_value - case_cost:+.2f})\n\n"
            f"*Added to your inventory.*\n"
            f"Your Total P/L: **£{current_profit_loss:.2f}** | Cases Opened: **{cases_opened_total}**"
        )
        embed = discord.Embed(title=f"You unboxed: {skin}",
                              description=result_description,
                              color=color_map.get(rarity, discord.Color.default()))

        if img_url:
            embed.set_image(url=img_url)
        else:
            embed.set_footer(text="Could not retrieve item image.")

        await message.edit(embed=embed)


    @commands.command(aliases=['inv', 'score'])
    async def inventory(self, ctx):
        """Displays your item inventory, score, and cases opened."""
        user_id = ctx.author.id
        user_entry = get_user_data_entry(user_id) # Ensures entry exists
        user_inv = user_entry.get("inventory", {})
        profit_loss = user_entry.get("profit_loss", 0.0)
        cases_opened = user_entry.get("cases_opened", 0) # Get cases opened

        embed = discord.Embed(title=f"{ctx.author.display_name}'s Inventory & Stats", color=discord.Color.green())

        embed.add_field(name="📊 Total Profit/Loss", value=f"**£{profit_loss:.2f}**", inline=True)
        embed.add_field(name="📦 Cases Opened", value=f"**{cases_opened}**", inline=True)

        if not user_inv:
            embed.description = "\nInventory is empty. Use `!case <Case Name>` to open cases!"
        else:
            description_lines = ["\n**Items:**"]
            # Sort items alphabetically for consistent display
            sorted_items = sorted(user_inv.items())
            for item_name, count in sorted_items:
                 # Bold count, regular item name
                description_lines.append(f"**{count}x** {item_name}")

            full_description = "\n".join(description_lines)
            # Handle potential description length limit (4096 chars)
            if len(full_description) > 4000: # Leave some buffer
                full_description = full_description[:4000] + "\n... (Inventory too large to display all)"
            embed.description = full_description
            # embed.set_footer(text="Item images and current values not shown here.") # Footer updated below

        # Add the recalculate button view
        view = InventoryView(original_user_id=ctx.author.id)
        message = await ctx.send(embed=embed, view=view)
        view.message = message # Store the message reference in the view


    @commands.command(aliases=['lb', 'top'])
    async def leaderboard(self, ctx, sort_by: str = 'profit', count: int = 10):
        """Shows the leaderboard. Sort by 'profit' (default) or 'cases'."""
        global user_data
        if not user_data:
             await ctx.send("No user data available to generate a leaderboard.")
             return

        if count > 25 or count < 1:
            await ctx.send("Please specify a count between 1 and 25.")
            return

        valid_sorts = ['profit', 'pl', 'score', 'cases', 'opened']
        sort_by = sort_by.lower()
        if sort_by not in valid_sorts:
             await ctx.send(f"Invalid sort option. Use 'profit' or 'cases'.")
             return

        # Create a list of tuples: (user_id, profit, cases_opened)
        leaderboard_data = []
        for uid, data in user_data.items():
            # Ensure data is valid and user has participated
            if isinstance(data, dict) and ("profit_loss" in data or "cases_opened" in data):
                 # Only include users who have opened at least one case or have non-zero profit
                 if data.get("cases_opened", 0) > 0 or data.get("profit_loss", 0.0) != 0.0:
                     leaderboard_data.append((
                         uid,
                         data.get("profit_loss", 0.0),
                         data.get("cases_opened", 0)
                     ))

        if not leaderboard_data:
            await ctx.send("Not enough data yet for a leaderboard (no one has opened cases or made profit/loss).")
            return

        # Sort the data
        if sort_by in ['profit', 'pl', 'score']:
            sorted_data = sorted(leaderboard_data, key=lambda x: x[1], reverse=True)
            sort_key_name = "Profit/Loss"
        else: # sort by cases
            sorted_data = sorted(leaderboard_data, key=lambda x: x[2], reverse=True)
            sort_key_name = "Cases Opened"


        embed = discord.Embed(title=f"🏆 Leaderboard - Top {min(count, len(sorted_data))} by {sort_key_name}", color=discord.Color.gold())

        lines = []
        rank = 1
        for uid, profit, cases in sorted_data[:count]:
            user = self.bot.get_user(uid) # Try to get user object
            user_name = user.display_name if user else f"User ID {uid}" # Fallback if user not found

            # Format line: Rank. User: P/L | Cases
            lines.append(f"{rank}. **{user_name}**: £{profit:,.2f} | {cases} cases")
            rank += 1

        if not lines:
             embed.description = "No users found for the leaderboard."
        else:
             embed.description = "\n".join(lines)

        await ctx.send(embed=embed)


# --- Cog for Slash Commands ---

# Generate choices dynamically, respecting Discord's limit of 25
case_choices = [
    app_commands.Choice(name=name, value=name)
    for name in list(all_cases.keys())[:25] # Take only the first 25
]
if len(all_cases) > 25:
    print("Warning: More than 25 cases defined, only the first 25 are available as slash command choices.")

class CaseSlashCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Share the session from the other cog if possible, or create a new one
        # For simplicity, we'll use the one from the main cog if available
        # This assumes CaseCommands cog is loaded first or accessible
        main_cog = bot.get_cog('CaseCommands')
        if main_cog and hasattr(main_cog, 'http_session'):
            self.http_session = main_cog.http_session
            print("CaseSlashCommands using shared HTTP session.")
        else:
            print("CaseSlashCommands creating its own HTTP session.")
            self.http_session = requests.Session()
        # Share rate limiter too
        self.last_api_call_time = 0
        self.api_call_delay = 1.5
        if main_cog and hasattr(main_cog, 'last_api_call_time'):
            # This is tricky, direct sharing might cause race conditions if not careful
            # Best might be to just have independent rate limiting per cog or a shared lock mechanism
            # For now, independent rate limiting state:
            self.last_api_call_time = 0
            self.api_call_delay = 1.5
            print("CaseSlashCommands using independent rate limiting state.")


    def cog_unload(self):
        """Close the session if this cog created it."""
        main_cog = self.bot.get_cog('CaseCommands')
        # Only close if the session doesn't belong to the main cog
        if not (main_cog and hasattr(main_cog, 'http_session') and self.http_session == main_cog.http_session):
             self.http_session.close()
             print("HTTP session closed for CaseSlashCommands.")

    async def check_api_rate_limit(self):
         """Simple delay-based rate limiting for Steam API calls (independent)."""
         now = asyncio.get_event_loop().time()
         time_since_last_call = now - self.last_api_call_time
         if time_since_last_call < self.api_call_delay:
             await asyncio.sleep(self.api_call_delay - time_since_last_call)
         self.last_api_call_time = asyncio.get_event_loop().time()


    @app_commands.command(name="case", description="Open a specified CS:GO case (£ cost varies), updates score & inventory.")
    @app_commands.describe(case_name="The name of the case you want to open")
    @app_commands.choices(case_name=case_choices) # Use the generated choices
    async def slash_case(self, interaction: discord.Interaction, case_name: str):
        """Slash command to open a CS:GO case."""
        user_id = interaction.user.id
        member = interaction.user # Get member object

        # Defer response early
        await interaction.response.defer(thinking=True, ephemeral=False) # Ephemeral=False makes it visible

        # Find the chosen case (exact match from choices)
        chosen_case_data = all_cases.get(case_name)
        chosen_case_name = case_name # Name is guaranteed by choices

        if not chosen_case_data:
            # This should technically not happen if choices are used correctly
            await interaction.followup.send(f"Error: Case data not found for '{case_name}'. This shouldn't happen.", ephemeral=True)
            return

        # --- Get Case Cost ---
        case_cost = chosen_case_data.get('cost', 0.0)
        if not isinstance(case_cost, (int, float)) or case_cost <= 0:
            await interaction.followup.send(f"Error: The cost for '{chosen_case_name}' is not configured correctly.")
            return

        # --- Increment cases opened and Deduct cost ---
        increment_cases_opened(user_id)
        update_user_score(user_id, -case_cost)
        save_user_data() # Save after score/count updates
        # ---

        # --- Determine Rarity, Base Skin, and Condition ---
        case_weights = chosen_case_data.get("weights")
        case_contents = chosen_case_data.get("contents")
        if not case_weights or not case_contents:
            await interaction.followup.send(f"Error: Configuration error for '{chosen_case_name}'. Missing weights or contents.")
            return

        rarity = weighted_random_choice(case_weights)
        if not rarity or rarity not in case_contents or not case_contents[rarity]:
            await interaction.followup.send(f"Error: Configuration error for '{chosen_case_name}'. Could not determine item pool for rarity '{rarity}'.")
            return

        base_skin = random.choice(case_contents[rarity])
        condition_suffix = weighted_random_choice(condition_chances)
        if not condition_suffix: condition_suffix = " (Field-Tested)" # Fallback
        skin = f"{base_skin}{condition_suffix}"
        # ---

        # --- !! BAN LOGIC !! ---
        if rarity == "Rare Special Item (Gold)" and ENABLE_BAN_ON_KNIFE:
            initial_embed = discord.Embed(title="🚨 RARE ITEM UNBOXED! 🚨", description=f"{member.mention} unboxed **{skin}** ({rarity}) from {chosen_case_name}! Initiating protocol...", color=discord.Color.gold())
            # Try to add thumbnail to initial message too
            await self.check_api_rate_limit()
            ban_img_url = await get_skin_image_url(skin, self.http_session)
            if ban_img_url: initial_embed.set_thumbnail(url=ban_img_url)

            # Use followup.send for the first message after deferral
            await interaction.followup.send(embed=initial_embed) # Send initial message
            await asyncio.sleep(2.5) # Dramatic pause
            try:
                await member.ban(reason=f"Unboxed a rare item ({skin}) from {chosen_case_name} via slash command!")
                # Edit the original deferred response (now the followup message)
                await interaction.edit_original_response(content=f"*{member.display_name} has been banned for unboxing a rare item.* Good luck!", embed=None, view=None) # Clear embed and view
                print(f"Banned {member.name} ({member.id}) for unboxing {skin} via slash command.")
                return # Stop processing
            except discord.Forbidden:
                await interaction.edit_original_response(content=f"⚠️ {member.mention} unboxed **{skin}**! I tried to ban them, but I lack the 'Ban Members' permission.", embed=None, view=None)
            except discord.HTTPException as e:
                 await interaction.edit_original_response(content=f"⚠️ {member.mention} unboxed **{skin}**! Failed to ban due to an API error: {e}", embed=None, view=None)
            except Exception as e:
                 await interaction.edit_original_response(content=f"⚠️ {member.mention} unboxed **{skin}**! An unexpected error occurred during the ban process: {e}", embed=None, view=None)
            # Continue if ban failed
        # --- !! END BAN LOGIC !! ---


        # --- Add item to inventory ---
        add_item_to_user_inventory(user_id, skin)
        # ---

        # --- Get Price and Image ---
        await self.check_api_rate_limit()
        price_str_task = asyncio.create_task(get_skin_price_str(skin, self.http_session))
        await self.check_api_rate_limit()
        img_url_task = asyncio.create_task(get_skin_image_url(skin, self.http_session))

        price_str = await price_str_task
        img_url = await img_url_task
        # ---

        # --- Calculate Value and Update Score ---
        item_value = parse_price(price_str) if price_str else 0.0
        if item_value > 0:
            update_user_score(user_id, item_value)
        save_user_data() # Save final state
        # ---

        # --- Prepare Result Embed ---
        color_map = {
            "Mil-Spec (Blue)": discord.Color.blue(), "Restricted (Purple)": discord.Color.purple(),
            "Classified (Pink)": discord.Color.magenta(), "Covert (Red)": discord.Color.red(),
            "Rare Special Item (Gold)": discord.Color.gold()
        }
        current_profit_loss = user_data.get(user_id, {}).get("profit_loss", 0.0)
        cases_opened_total = user_data.get(user_id, {}).get("cases_opened", 0)

        result_description = (
            f"Opened: **{chosen_case_name}** (Cost: £{case_cost:.2f})\n"
            f"Rarity: **{rarity}** | Market Value: **{price_str or 'Unknown'}** (P/L this item: £{item_value - case_cost:+.2f})\n\n"
            f"*Added to inventory. Use `!inventory` to view.*\n"
            f"Your Total P/L: **£{current_profit_loss:.2f}** | Cases Opened: **{cases_opened_total}**"
        )
        embed = discord.Embed(title=f"You unboxed: {skin}",
                              description=result_description,
                              color=color_map.get(rarity, discord.Color.default()))

        if img_url:
            embed.set_image(url=img_url)
        else:
            embed.set_footer(text="Could not retrieve item image.")

        # Edit the original deferred response (followup message)
        await interaction.edit_original_response(embed=embed, view=None) # view=None ensures no lingering components


# --- Bot Events and Setup ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print(f'Discord.py version: {discord.__version__}')
    print('------')
    load_user_data() # Load data on ready
    await setup_cogs() # Setup cogs after ready seems safer

async def setup_cogs():
    """Registers cogs with the bot and syncs slash commands."""
    print("Setting up cogs...")
    # Add Cogs
    await bot.add_cog(CaseCommands(bot))
    print("CaseCommands cog added.")
    await bot.add_cog(CaseSlashCommands(bot))
    print("CaseSlashCommands cog added.")

    # Sync slash commands (usually done here or in on_ready)
    try:
        # Sync globally if intended for all guilds, or specify guild ID for testing
        # synced = await bot.tree.sync()
        # Example: Sync to a specific guild for faster updates during testing
        # test_guild_id = 123456789012345678 # Replace with your test server ID
        # synced = await bot.tree.sync(guild=discord.Object(id=test_guild_id))
        synced = await bot.tree.sync() # Sync globally
        print(f'Synced {len(synced)} application commands.')
        for cmd in synced:
            print(f'- Synced: {cmd.name} ({cmd.type})') # Show type (1=slash, 2=user, 3=message)
    except discord.errors.Forbidden as e:
        print(f"Error syncing slash commands: Missing Permissions. Ensure the bot has the 'application.commands' scope. Details: {e}")
    except discord.HTTPException as e:
         print(f"Error syncing slash commands: HTTP error. {e}")
    except Exception as e:
        print(f"An unexpected error occurred during slash command sync: {e}")


# Optional: Run setup using bot.setup_hook for newer discord.py versions
# async def setup_hook():
#     await setup_cogs()
# bot.setup_hook = setup_hook


if __name__ == "__main__":
    # Load token from environment variable or config file is recommended
    # Avoid hardcoding tokens in scripts
    BOT_TOKEN = "" # Example: Load from environment variable
    if not BOT_TOKEN:
        # Fallback for testing - **REPLACE THIS WITH A SECURE METHOD**
        # BOT_TOKEN = 'YOUR_FALLBACK_TOKEN_HERE_FOR_TESTING_ONLY'
        print("Warning: Bot token not found in environment variable DISCORD_BOT_TOKEN.")
        # Attempt to read from a file named 'token.txt' in the same directory
        try:
            with open('token.txt', 'r') as f:
                BOT_TOKEN = f.read().strip()
            if not BOT_TOKEN:
                 print("ERROR: token.txt is empty.")
            else:
                 print("Loaded token from token.txt")
        except FileNotFoundError:
             print("ERROR: Could not find token.txt and DISCORD_BOT_TOKEN is not set.")
             BOT_TOKEN = None # Ensure it's None if not found
        except Exception as e:
             print(f"Error reading token.txt: {e}")
             BOT_TOKEN = None


    if BOT_TOKEN:
        try:
            # Consider using asyncio.run() for the main entry point
            # asyncio.run(bot.start(BOT_TOKEN))
            # Or the traditional bot.run()
            bot.run(BOT_TOKEN)
        except discord.errors.LoginFailure:
            print("ERROR: Invalid bot token provided. Please check the token.")
        except discord.errors.PrivilegedIntentsRequired:
             print("ERROR: Privileged intents (Members, Message Content) are not enabled for the bot in the Discord Developer Portal.")
        except Exception as e:
            print(f"An critical error occurred while running the bot: {e}")
            # Consider more detailed logging here
            raise # Reraise exception for traceback
    else:
        print("ERROR: Bot token is missing. Bot cannot start.")
        print("Please set the DISCORD_BOT_TOKEN environment variable or create a 'token.txt' file.")

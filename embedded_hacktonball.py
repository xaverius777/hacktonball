import base64
import queue
import re
import threading
import time
from io import BytesIO

import keyboard
import pygame
import pytesseract
from PIL import Image
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

CODE_DIR = os.path.join(BASE_DIR, "code")
CODE_PATH = CODE_DIR

AUDIO_DIR = os.path.join(BASE_DIR, "audio")

NEW_RECORD_SFX = os.path.join(AUDIO_DIR, "NEW RECORD.wav")
GAME_OVER_SFX = os.path.join(AUDIO_DIR, "GAME OVER.wav")
BEGIN_SFX = os.path.join(AUDIO_DIR, "BEGGIN.wav")
COLLISION_SFX = os.path.join(AUDIO_DIR, "COLISION.wav")
JUMP_SFX = os.path.join(AUDIO_DIR, "JUMP.wav")

INTRO_TRACK = os.path.join(AUDIO_DIR, "MENU SPNG.wav")
MAIN_TRACK = os.path.join(AUDIO_DIR, "MAIN THEME OST.wav")

# We access the Hack RAM directly by its selector on the VM emulator website
RAM_PANEL_SELECTOR = "article.panel.memory.RAM"
def read_ram_address(driver, ram_addr):
    return driver.execute_script("""
        const el = document.querySelector(arguments[0]);
        const reactPropsKey = Object.keys(el).find(k => k.startsWith("__reactProps$"));
        const ram = el[reactPropsKey].children[1].props.memory.parent.memory;
        return ram[arguments[1]];
    """, RAM_PANEL_SELECTOR, ram_addr)

chrome_options = Options()
driver = webdriver.Chrome(options=chrome_options)
# Fixed window size, so the setup is reproducible.
# WARNING: do NOT touch! Things depend on this setup, like the OCR that allows us
# to add SFX into the game!
driver.set_window_size(1200, 800)
driver.get("https://nand2tetris.github.io/web-ide/compiler")

# Try finding the element until we do find it. Selenium provides a native way to the library to do that
# but it can be done this way as well
no_attempts = 50
for attempt in range(0, no_attempts):
    try:
        folder_input = driver.find_element(By.XPATH, '/html/body/div/main/div/input')
        break
    except NoSuchElementException:
        time.sleep(1)
        continue
# Compile source Jack code
folder_input.send_keys(CODE_PATH)
compile_button = driver.find_element(By.XPATH, '/html/body/div[1]/main/div/article/header/div[2]/button[3]')
compile_button.click()
run_button = driver.find_element(By.XPATH, '/html/body/div[1]/main/div/article/header/div[2]/button[4]')
run_button.click()

for attempt in range(0, no_attempts):
    try:
        google_analytics_button = driver.find_element(By.XPATH, '/html/body/div[1]/div/div[2]/a[2]')
        break
    except NoSuchElementException:
        time.sleep(1)
        continue

# Run the Jack code
google_analytics_button.click()
enable_keyboard_button = driver.find_element(By.XPATH, '/html/body/div[1]/main/div/article[3]/main/article[2]/div/button')
enable_keyboard_button.click()
run_game_button = driver.find_element(By.XPATH, '/html/body/div[1]/main/div/article[1]/header/div[2]/div/fieldset/button[3]')
run_game_button.click()
slider = WebDriverWait(driver, 5).until(
    EC.presence_of_element_located((By.XPATH, '/html/body/div[1]/main/div/article[1]/header/div[2]/div/div[2]/input'))
)
# We can't manipulate the elements directly because React does React things.
# TODO: find the correct explanation for why this happens.
driver.execute_script("""
const slider = arguments[0];
const max = slider.max;
slider.removeAttribute('disabled');
const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value'
).set;
nativeInputValueSetter.call(slider, max);
slider.dispatchEvent(new Event('input', { bubbles: true }));
slider.dispatchEvent(new Event('change', { bubbles: true }));
""", slider)
x2_hack_screen_button = driver.find_element(By.XPATH, '/html/body/div[1]/main/div/article[3]/main/article[1]/header/fieldset/button[3]')
x2_hack_screen_button.click()
driver.execute_script("document.body.style.zoom='100%'")

# Once the Jack game is set up, let's do the Python side, which adds sound to the gameplay

# A queue for tracks and another one for SFX
music_queue = queue.Queue()
sfx_queue = queue.Queue()

pygame.mixer.init()

# Global variables we will use. wall_event_addr is EXTREMELY important! It's the memory address
# of the variable that expresses the game state variable the SFX sounds are wired to!
game_started = False
wall_event_addr = None

def music_thread():
    pygame.mixer.music.load(INTRO_TRACK)
    pygame.mixer.music.set_volume(1.0)
    pygame.mixer.music.play(-1)

    while True:
        event = music_queue.get()

        if event == "play_main":
            pygame.mixer.music.load(MAIN_TRACK)
            pygame.mixer.music.set_volume(1.0)
            pygame.mixer.music.play(-1)

        # Pause the music and then resume it with a lower volume, to audibly convey
        # the fact that the main action has gone to the background
        elif event == "game_over_music" or event == "new_record_music":
            pygame.mixer.music.pause()
            time.sleep(3)
            pygame.mixer.music.set_volume(0.35)
            pygame.mixer.music.unpause()

        elif event == "volume_full":
            pygame.mixer.music.set_volume(1.0)

        elif event == "stop":
            pygame.mixer.music.stop()
            break

def sfx_thread():
    jump_sfx = pygame.mixer.Sound(JUMP_SFX)
    collision_sfx = pygame.mixer.Sound(COLLISION_SFX)
    begin_sfx = pygame.mixer.Sound(BEGIN_SFX)
    game_over_sfx = pygame.mixer.Sound(GAME_OVER_SFX)
    new_record_sfx = pygame.mixer.Sound(NEW_RECORD_SFX)

    while True:
        event = sfx_queue.get()

        if event == "jump":
            jump_sfx.play()

        elif event == "collision":
            collision_sfx.play()
        
        elif event == "begin":
            begin_sfx.play()

        elif event == "game_over":
            game_over_sfx.play()
        
        elif event == "new_record":
            new_record_sfx.play()

        elif event == "stop":
            break

# WARNING: assumes the player has pressed enter, so the ball object will have been allocated
# This function uses Selenium and JS to get the memory address of the wall event variable.
# It's clunky and ugly but it gets the job done.
def locate_wall_event_addr():
    # Tesseract path.
    # WARNING: for this to work, you need to install tesseract. If you don't want to use tesseract,
    # you can use another OCR software of course
    tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

    # Get the Hack computer's screen, as an image
    canvas_xpath = "/html/body/div[1]/main/div/article[3]/main/article[1]/main/figure/canvas"
    canvas = driver.find_element(By.XPATH, canvas_xpath)
    data_url = driver.execute_script("""
        const canvas = arguments[0];
        return canvas.toDataURL("image/png");
    """, canvas)

    png_b64 = data_url.split(",", 1)[1]
    img = Image.open(BytesIO(base64.b64decode(png_b64)))
    # Crop the memory address out of the screenshot. This is why the screen dimensions
    # are hardcoded, they are essential for this to work
    ball_ptr_crop_box = (380, 105, 415, 120)
    crop = img.crop(ball_ptr_crop_box)
    # Get the text of the image, that is, the integer, with Tesseract
    text = pytesseract.image_to_string(crop)
    # Convert the text to the integer
    match = re.search(r"\d+", text)
    ball_ptr = int(match.group())
    # Get the entire Hack RAM lmao, all 24,5K cells of it. Don't worry, this is a cold path.
    hack_ram_contents = driver.execute_script("""
            const el = document.querySelector(arguments[0]);
            const reactPropsKey = Object.keys(el).find(k => k.startsWith("__reactProps$"));
            const ram = el[reactPropsKey].children[1].props.memory.parent.memory;
            return Array.from(ram);
        """, RAM_PANEL_SELECTOR)
    # Iterate over the cells until we see a cell whose value is the memory address of the object.
    # This works because we're getting the variable that is in the entry point of the Jack program, so
    # we're guaranteed (until proven otherwise) to get the right cell.
    for addr, value in enumerate(hack_ram_contents):
        if value == ball_ptr:
            ball_addr = addr
            break
    # Retrieve the wall event address by using the cell number to navigate to nearby memory cells,
    # which are populated with the main function's local variables
    wall_event_addr = ball_addr + 7
    return wall_event_addr

# If no the window has no __hacktonball object, return an empty array.
# If it does, get the events key from it and then empty the events list before returning the
# extracted event
def get_pending_events(driver):
    return driver.execute_script("""
        if (!window.__hacktonball) {
            return [];
        }

        const events = window.__hacktonball.events.slice();
        window.__hacktonball.events.length = 0;
        return events;
    """)

def on_s_pressed():
    global game_started
    global wall_event_addr
    # Always play SFX
    sfx_queue.put("begin")
    music_queue.put("volume_full")

    if not game_started:
        game_started = True
        music_queue.put("play_main")
    else:
        pass
    # In sync with Jack's startup/reset pointer exposure
    try:
        wall_event_addr = locate_wall_event_addr()
        # Install a browser-side RAM poller.
        #
        # Selenium injects vanilla JS into the emulator page. The only React-specific part
        # is the object traversal needed to reach the emulator's internal RAM array.
        #
        # Flow:
        # 1. Receive the RAM panel selector and target RAM address from Python.
        # 2. Stop any previously installed poller.
        # 3. Locate the RAM panel DOM element.
        # 4. Find React's private props object attached to that DOM element.
        # 5. Walk through the React object graph until reaching the live Hack RAM array.
        # 6. Store the current value as the initial state.
        # 7. Poll the target RAM cell every 1 ms.
        # 8. Whenever the value changes, enqueue an event for Python to consume.
        # This keeps high-frequency polling inside the browser, where reading the RAM
        # array is cheap. Python only periodically drains the accumulated event queue,
        # avoiding expensive repeated Selenium round-trips.
        driver.execute_script("""
            const selector = arguments[0];
            const wallEventAddr = arguments[1];

            if (window.__hacktonballPoller) {
                clearInterval(window.__hacktonballPoller);
            }

            const el = document.querySelector(selector);
            const reactPropsKey = Object.keys(el).find(k => k.startsWith("__reactProps$"));
            const ram = el[reactPropsKey].children[1].props.memory.parent.memory;

            const initialEvent = ram[wallEventAddr];

            window.__hacktonball = {
                ram,
                wallEventAddr,
                events: [],
                previousEvent: initialEvent
            };

            window.__hacktonballPoller = setInterval(() => {
                const event = window.__hacktonball.ram[
                    window.__hacktonball.wallEventAddr
                ];

                if (event !== window.__hacktonball.previousEvent) {
                    window.__hacktonball.events.push({
                        event,
                        t: performance.now()
                    });

                    window.__hacktonball.previousEvent = event;
                }
                }, 1);
        """, RAM_PANEL_SELECTOR, wall_event_addr)
    except Exception as e:
        print("Failed to locate wallEvent address:", repr(e))


# Start the music and SFX threads
threading.Thread(target=music_thread, daemon=True).start()
threading.Thread(target=sfx_thread, daemon=True).start()

# This block implements debounce for the space (the jump key), so it won't sound
# repeatedly when you hold
space_is_down = False
def on_space_pressed():
    global space_is_down

    if not space_is_down:
        space_is_down = True
        sfx_queue.put("jump")

def on_space_released():
    global space_is_down
    space_is_down = False

# Key wiring
keyboard.on_press_key("s", lambda _: on_s_pressed())
keyboard.on_press_key("space", lambda _: on_space_pressed())
keyboard.on_release_key("space", lambda _: on_space_released())
print("Press Q to quit...")

# Main loop, in which we wire the wall event to music and SFX
try:
    while True:
        if keyboard.is_pressed("q"):
            break

        events = get_pending_events(driver)

        for item in events:
            event = item["event"]
            # print("EVENT:", event)
            if event in (1, 3, 4):
                sfx_queue.put("collision")

            elif event == 2:
                # Read another Hack RAM memory address that contains a variable that indicates
                # whether a new record was achieved or not
                new_record_addr = wall_event_addr - 4
                new_record = read_ram_address(driver, new_record_addr)

                if new_record == -1:
                    music_queue.put("new_record_music")
                    sfx_queue.put("new_record")
                else:
                    music_queue.put("game_over_music")
                    sfx_queue.put("game_over")
            
        time.sleep(0.01)

finally:
    music_queue.put("stop")
    sfx_queue.put("stop")

    pygame.mixer.quit()
    driver.quit()
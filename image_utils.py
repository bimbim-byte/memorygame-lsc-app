import cv2
import numpy as np
import os
import math
from PIL import Image, ImageDraw, ImageFont

def make_transparent(canvas, x, y, box_size=52):
    canvas[y:y+box_size, x:x+box_size, 3] = 0

def get_input_filename(index: int) -> str:
    return f"Screenshot_{math.ceil(index / 2)}.png"

def compare_images(img1, img2):
    img1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    img2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    img1 = cv2.resize(img1, (100, 100))
    img2 = cv2.resize(img2, (100, 100))
    diff = cv2.absdiff(img1, img2)
    return 1 - (np.sum(diff) / (100*100*255))

def create_overlay_images(matched_pairs, offsets, output_folder, screen_width, screen_height, mkx1, mky1, mkx2, mky2, box_size=52):
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except:
        font = ImageFont.load_default()
    for idx, (a, b) in enumerate(matched_pairs, start=0):
        canvas = np.zeros((screen_height, screen_width, 4), dtype=np.uint8)
        canvas[mky1:mky2, mkx1:mkx2, 0:3] = 0
        canvas[mky1:mky2, mkx1:mkx2, 3] = int(255 * 0.50)
        for card in [a+1, b+1]:
            pos = offsets.get(str(card))
            if pos:
                make_transparent(canvas, pos["x"], pos["y"], box_size)
        text = "Alt + Z: Prev | Alt + X: Next"
        pil_img = Image.fromarray(canvas)
        draw = ImageDraw.Draw(pil_img)
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        x = (screen_width - text_w) // 2
        y = 10
        padding = 10
        rect_x1 = x - padding
        rect_y1 = y - padding
        rect_x2 = x + text_w + padding
        rect_y2 = y + text_h + padding
        draw.rectangle(
            [rect_x1, rect_y1, rect_x2, rect_y2],
            fill=(0, 0, 0, 180)
        )
        draw.text((x, y), text, font=font, fill=(255, 255, 0, 255))

        path = os.path.join(output_folder, f"pair_{idx+1}.png")
        pil_img.save(path, "PNG")

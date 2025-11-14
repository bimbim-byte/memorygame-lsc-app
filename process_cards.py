import os
import cv2
import numpy as np
from image_utils import compare_images, get_input_filename

class ImageProcessor:
    def __init__(self, input_folder, process_folder, box_size=52):
        self.input_folder = input_folder
        self.process_folder = process_folder
        self.box_size = box_size

    def process_cropping(self, offsets):
        os.makedirs(self.process_folder, exist_ok=True)
        
        for key, coords in offsets.items():
            index = int(key)
            x, y = coords["x"], coords["y"]
            input_file = get_input_filename(index)
            input_path = os.path.join(self.input_folder, input_file)
            output_path = os.path.join(self.process_folder, f"{index}.png")

            # Baca gambar
            img = cv2.imread(input_path)
            if img is None:
                print(f"⚠️ Gagal membaca {input_path}")
                continue

            # Crop area
            crop_img = img[y:y+self.box_size, x:x+self.box_size]

            # Simpan hasil crop
            cv2.imwrite(output_path, crop_img)

    def process_matching(self, threshold):
        cards = []
        for i in range(1, 31):
            path = os.path.join(self.process_folder, f"{i}.png")
            if os.path.exists(path):
                img = cv2.imread(path)
                if img is not None:
                    cards.append((i, img))

        matched_pairs = []
        checked = set()

        for i in range(len(cards)):
            if i in checked:
                continue
            for j in range(i + 1, len(cards)):
                score = compare_images(cards[i][1], cards[j][1])
                if score > threshold:
                    matched_pairs.append((i, j))
                    checked.update({i, j})
                    break

        return matched_pairs

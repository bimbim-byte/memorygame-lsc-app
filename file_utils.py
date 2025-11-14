import os
import shutil

def load_image_paths(folder):
    paths = []
    i = 1
    while True:
        p = os.path.join(folder, f"pair_{i}.png")
        if os.path.exists(p):
            paths.append(p)
            i += 1
        else:
            break
    if not paths and os.path.isdir(folder):
        files = sorted([f for f in os.listdir(folder) if f.lower().endswith(".png")])
        paths = [os.path.join(folder, f) for f in files]
    return paths

def clear_folder(folder_path):
    if not os.path.exists(folder_path):
        return
    for filename in os.listdir(folder_path):
        path = os.path.join(folder_path, filename)
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.unlink(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
        except Exception as e:
            print(f"Gagal menghapus {path}: {e}")

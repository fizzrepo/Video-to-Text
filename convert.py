import cv2
import numpy as np
import multiprocessing
from joblib import Parallel, delayed

ASPECT_RATIO = 16 / 9

# Dimensions of the output in terminal characters
WIDTH = 80
HEIGHT = int(WIDTH / (2 * ASPECT_RATIO))

# Framerate of the source and output video
SRC_FPS = 30
DEST_FPS = 15


NUM_CORES = multiprocessing.cpu_count()

cap = cv2.VideoCapture('vid.mp4')
frames = []

# Our characters, and their approximate brightness values
CHARSET = ' ,(S#g@'
LEVELS = [0.000, 1.060, 2.167, 3.036, 3.977, 4.730, 6.000]
NUMCHARS = len(CHARSET)


def process_frame(scaled):
    '''
        Converts a greyscale video frame into a dithered 7-color frame
    '''
    
    reduced = scaled * 6. / 255
    
    out = np.zeros((HEIGHT, WIDTH), dtype=np.int8)
    
    for y in range(HEIGHT):
        for x in range(WIDTH):
            level = min(6, max(0, int(reduced[y, x])))
            
            error = reduced[y, x] - LEVELS[level]
    
            err16 = error / 16
    
            if (x + 1) < WIDTH:
                reduced[y    , x + 1] += 7 * err16
            if (y + 1) < HEIGHT:
                reduced[y + 1, x    ] += 5 * err16
    
                if (x + 1) < WIDTH:
                    reduced[y + 1, x + 1] += 1 * err16
                if (x - 1) > 0:
                    reduced[y + 1, x - 1] += 3 * err16
            
            out[y, x] = level

    return out

def frame_to_str(frame):
    '''
        Prints out a frame in ASCII
    '''

    line = ''
    
    for y in range(HEIGHT):
        for x in range(WIDTH):
            line += CHARSET[frame[y, x]]
        line += '\n'
    
    return line

def compute_markov(frame):
    '''
        Compute the prediction matrix for each character combination
        Each row in this matrix corresponds with a character, and lists
        in decreasing order, the next most likely character to follow this one

        We also convert the provided frame to this new markov encoding, and provide
        the count of each prediction rank to be passed to the huffman encoding
    '''

    mat = np.zeros((NUMCHARS, NUMCHARS)).astype(np.uint16)

    h, w = frame.shape

    prevChar = 0

    for y in range(h):
        for x in range(w):
            char = frame[y, x]

            mat[prevChar, char] += 1

            prevChar = char
    
    ranks = np.zeros((NUMCHARS, NUMCHARS)).astype(np.uint16)
    for i in range(NUMCHARS):
        ranks[i][mat[i].argsort()] = 6 - np.arange(NUMCHARS)

    cnt = np.zeros(NUMCHARS).astype(np.uint16)

    out = np.zeros_like(frame)
    prevChar = 0
    for y in range(h):
        for x in range(w):
            char = frame[y, x]

            out[y, x] = ranks[prevChar, char]
            cnt[out[y, x]] += 1

            prevChar = char
    
    return out, ranks, cnt

def compute_huffman(cnts):
    '''
        Computes Huffman encodings based on the counts of each number in the frame
    '''

    codes = []
    sizes = []
    tree = []
    for i in range(len(cnts)):
        codes.append('')
        sizes.append((cnts[i], [i], i))
        tree.append((i, i))

    sizes = sorted(sizes, reverse=True)

    while len(sizes) > 1:
        # Take the two least frequent entries
        right = sizes.pop()
        left  = sizes.pop()

        (lnum, lchars, ltree) = left
        (rnum, rchars, rtree) = right

        # Add a new tree node
        tree.append((ltree, rtree))

        # Update the encodings
        for char in lchars:
            codes[char] = '0' + codes[char]
        for char in rchars:
            codes[char] = '1' + codes[char]

        # Merge these entries
        new = (lnum + rnum, lchars + rchars, len(tree) - 1)

        # Find the position in the list to inser these entries
        for insertPos in range(len(sizes) + 1):
            # Append if we hit the end of the list
            if insertPos == len(sizes):
                sizes.append(new)
                break
                
            cnt, _, _ = sizes[insertPos]
            
            if cnt <= lnum + rnum:
                sizes.insert(insertPos, new)
                break

    return codes, tree


def convert_huffman(markov_frame, codes):
    '''
        Take a markov frame and an array of huffman encodings, and create an array of
        bytes corresponding to the compressed frame
    '''

    out = ''

    h, w = frame.shape

    for y in range(h):
        for x in range(w):
            out = out + codes[markov_frame[y, x]]
    
    # Pad this bit-string to be byte-aligned
    padding = (8 - (len(out) % 8)) % 8
    out += '0' * padding

    # Convert each octet to a char
    compressed = []
    for i in range(0, len(out), 8):
        byte = out[i:i+8]
        char = 0
        for bit in range(8):
            char *= 2
            if byte[bit] == '1':
                char += 1

        compressed.append(char)

    return compressed

def encode_matrix(ranks):
    '''
        Converts a rank matrix into a binary format to be stored in the output file
    '''

    out = []

    for row in ranks:
        encoding = 0

        fact = 1
        idxs = list(range(len(CHARSET)))

        for rank in range(len(CHARSET)):
            rank = list(row).index(rank)
            encoding += idxs.index(rank) * fact

            fact *= len(idxs)
            idxs.remove(rank)
        
        low_byte = int(encoding) % 256
        high_byte = (encoding - low_byte) // 256
        
        out.append(high_byte)
        out.append(low_byte)

    return out

def encode_tree(tree):
    '''
        Converts the huffman tree into a binary format to be stored in the output file
    '''

    tree = tree[len(CHARSET):]

    out = []

    for (l, r) in tree:
        out.append(l * 16 + r)

    return out

# Load all frames into memory, then convert them to greyscale and resize them to
# our terminal dimensions
vid_frames = []
while cap.isOpened():
    if (len(vid_frames) % 500) == 0:
        print(f'Loading frame {len(vid_frames)}')
    
    # Skip frames to reach target framerate
    for i in range(int(SRC_FPS / DEST_FPS)):
        ret, frame = cap.read()
    
    if frame is None:
        break
    
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    scaled = cv2.resize(gray, (WIDTH, HEIGHT))
    
    vid_frames.append(scaled)

# Compute dithering for all frames in parallel
print('Dithering Frames')
frames = Parallel(n_jobs=NUM_CORES)(delayed(process_frame)(i) for i in vid_frames)

# Compute markov and huffman encoding for all frames
print('Encoding Frames')
out = ''
size = 0

with open('data', 'wb') as filehandle:
    for frame in frames:
        markov_frame, ranks, cnts = compute_markov(frame)

        codes, tree = compute_huffman(cnts)
        chars = convert_huffman(markov_frame, codes)

        matrix_data = encode_matrix(ranks)
        tree_data = encode_tree(tree)

        filehandle.write(bytearray(matrix_data))
        filehandle.write(bytearray(tree_data))
        filehandle.write(bytearray(chars))

        size += len(matrix_data) + len(tree_data) + len(chars)

# Print the size of the output file in human-readable form
if size > 1048576:
    print(f'{size / 1048576:.1f} MB')
elif size > 1024:
    print(f'{size / 1024:.1f} kB')
else:
    print(f'{size} B')

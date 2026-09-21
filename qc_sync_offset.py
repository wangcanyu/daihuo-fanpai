#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SyncNet offset probe, bypassing official pipeline (no dlib/s3fd).
# Video frames -> 25fps -> mediapipe FaceMesh face bbox -> official crop geometry -> 224x224 BGR raw
# Audio -> 16k mono -> python_speech_features.mfcc (13 x 100fps)
# Feeds SyncNetModel.S exactly like SyncNetInstance.evaluate (5-frame stacks + 20-mfcc windows)

import os, sys, math, subprocess, argparse, json
import numpy as np
import cv2
import torch
import mediapipe as mp
from scipy import signal
from scipy.io import wavfile
import python_speech_features

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'syncnet_python'))
from SyncNetModel import S

CROP_SCALE = 0.40          # official crop_scale
BATCH = 20


def ffmpeg_to_25fps(video, out_avi):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video,
                    "-qscale:v", "2", "-async", "1", "-r", "25", out_avi], check=True)


def ffmpeg_audio_16k(audio, out_wav):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", audio,
                    "-ac", "1", "-acodec", "pcm_s16le", "-ar", "16000", out_wav], check=True)


def face_tracks_mediapipe(frames):
    """Per-frame face bbox; returns list of contiguous detected runs (start, end, bboxes)."""
    fm = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=1,
                                         refine_landmarks=False, min_detection_confidence=0.5)
    bboxes = []
    for fr in frames:
        rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
        res = fm.process(rgb)
        bb = None
        if res.multi_face_landmarks:
            lm = res.multi_face_landmarks[0].landmark
            h, w = fr.shape[:2]
            xs = [p.x * w for p in lm]
            ys = [p.y * h for p in lm]
            bb = (min(xs), min(ys), max(xs), max(ys))
        bboxes.append(bb)
    fm.close()
    runs, s = [], None
    for i, b in enumerate(bboxes):
        if b is not None and s is None:
            s = i
        if b is None and s is not None:
            runs.append((s, i-1))
            s = None
    if s is not None:
        runs.append((s, len(bboxes)-1))
    return bboxes, runs


def crop_mouth_track(frames, bboxes):
    """Replicates run_pipeline.crop_video geometry: center=(bbox center), s=max(h,w)/2,
    crop [y-s : y+s*(1+2cs), x-s*(1+cs) : x+s*(1+cs)], medfilt(13) smoothing, resize 224x224."""
    ss = np.array([max(b[3]-b[1], b[2]-b[0])/2 for b in bboxes])
    ys = np.array([(b[1]+b[3])/2 for b in bboxes])
    xs = np.array([(b[0]+b[2])/2 for b in bboxes])
    ss = signal.medfilt(ss, kernel_size=13)
    ys = signal.medfilt(ys, kernel_size=13)
    xs = signal.medfilt(xs, kernel_size=13)

    cs = CROP_SCALE
    crops = []
    for i, fr in enumerate(frames):
        bs = ss[i]
        bsi = int(bs*(1+2*cs))
        padded = np.pad(fr, ((bsi, bsi), (bsi, bsi), (0, 0)), 'constant', constant_values=(110, 110))
        my, mx = ys[i]+bsi, xs[i]+bsi
        face = padded[int(my-bs):int(my+bs*(1+2*cs)), int(mx-bs*(1+cs)):int(mx+bs*(1+cs))]
        crops.append(cv2.resize(face, (224, 224)))
    return crops


def calc_pdist(feat1, feat2, vshift):
    win = vshift*2+1
    feat2p = torch.nn.functional.pad(feat2, (0, 0, vshift, vshift))
    dists = []
    for i in range(len(feat1)):
        dists.append(torch.nn.functional.pairwise_distance(
            feat1[[i], :].repeat(win, 1), feat2p[i:i+win, :]))
    return dists


def run(video, audio, weights, vshift=15, tmpdir=".", dump_crops=None, track=None):
    os.makedirs(tmpdir, exist_ok=True)
    avi = os.path.join(tmpdir, "v25.avi")
    wav = os.path.join(tmpdir, "a16k.wav")
    ffmpeg_to_25fps(video, avi)
    ffmpeg_audio_16k(audio, wav)

    cap = cv2.VideoCapture(avi)
    frames = []
    while True:
        ret, im = cap.read()
        if not ret:
            break
        frames.append(im)
    cap.release()

    bboxes, runs = face_tracks_mediapipe(frames)
    if track is not None:
        f0, f1 = track
    else:
        if not runs:
            raise RuntimeError("no face track found")
        f0, f1 = max(runs, key=lambda r: r[1]-r[0])
    track_frames = frames[f0:f1+1]
    track_bboxes = bboxes[f0:f1+1]
    crops = crop_mouth_track(track_frames, track_bboxes)

    if dump_crops:
        os.makedirs(dump_crops, exist_ok=True)
        for i in range(0, len(crops), max(1, len(crops)//6)):
            cv2.imwrite(os.path.join(dump_crops, "crop_%04d.jpg" % (f0+i)), crops[i])

    im = np.stack(crops, axis=3)                    # H W C T
    im = np.transpose(im, (2, 3, 0, 1))[None]       # 1 C T H W
    imtv = torch.from_numpy(im.astype(float)).float()

    sr, aud = wavfile.read(wav)
    # slice audio to the face-track time range (25fps grid)
    a0 = int(round(f0 / 25.0 * sr))
    a1 = int(round((f1 + 1) / 25.0 * sr))
    aud = aud[a0:min(a1, len(aud))]
    mfcc = zip(*python_speech_features.mfcc(aud, sr))
    mfcc = np.stack([np.array(i) for i in mfcc])    # 13 x T100
    cct = torch.from_numpy(mfcc.astype(float)).float()[None, None]

    min_length = min(len(crops), math.floor(len(aud)/640))
    lastframe = min_length - 5
    if lastframe <= 0:
        raise RuntimeError("clip too short")

    model = S(num_layers_in_fc_layers=1024)
    state = torch.load(weights, map_location='cpu', weights_only=True)
    model.load_state_dict(state)
    model.eval()

    im_feat, cc_feat = [], []
    with torch.no_grad():
        for i in range(0, lastframe, BATCH):
            v0, v1 = i, min(lastframe, i+BATCH)
            im_in = torch.cat([imtv[:, :, v:v+5] for v in range(v0, v1)], 0)
            im_feat.append(model.forward_lip(im_in).cpu())
            cc_in = torch.cat([cct[:, :, :, v*4:v*4+20] for v in range(v0, v1)], 0)
            cc_feat.append(model.forward_aud(cc_in).cpu())
    im_feat = torch.cat(im_feat, 0)
    cc_feat = torch.cat(cc_feat, 0)

    dists = calc_pdist(im_feat, cc_feat, vshift)
    mdist = torch.mean(torch.stack(dists, 1), 1)
    minval, minidx = torch.min(mdist, 0)
    offset = vshift - minidx.item()
    conf = (torch.median(mdist) - minval).item()

    # top-3 offsets for stability check
    vals, idxs = torch.topk(mdist, 3, largest=False)
    top3 = [(vshift - idxs[k].item(), round(vals[k].item(), 3)) for k in range(3)]

    return {
        "offset_frames": offset,
        "offset_sec": round(offset / 25.0, 3),
        "confidence": round(conf, 3),
        "min_dist": round(minval.item(), 3),
        "n_frames": lastframe,
        "face_track_25fps": [f0, f1],
        "face_runs": runs,
        "top3": top3,
        "mdist_curve": [round(v, 3) for v in mdist.tolist()],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--weights", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "syncnet_v2.model"))
    ap.add_argument("--vshift", type=int, default=15)
    ap.add_argument("--track", default=None, help="e.g. 2:42, frame range at 25fps; default=longest face run")
    ap.add_argument("--tmpdir", default=None)
    ap.add_argument("--dump_crops", default=None)
    a = ap.parse_args()
    track = tuple(int(x) for x in a.track.split(":")) if a.track else None
    tmp = a.tmpdir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    out = run(a.video, a.audio, a.weights, a.vshift, tmp, a.dump_crops, track)
    curve = out.pop("mdist_curve")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("mdist curve (idx0 = -vshift):", curve)

import logging
import os
import sys
import traceback

from handlers.config import model_path

from functools import lru_cache
from time import time as ttime

import faiss
import librosa
import numpy as np
import parselmouth
import pyworld
import torch
import torch.nn.functional as F
import torchcrepe
from scipy import signal

logger = logging.getLogger(__name__)

bh, ah = signal.butter(N=5, Wn=48, btype="high", fs=16000)

input_audio_path2wav = {}


@lru_cache
def cache_harvest_f0(input_audio_path, fs, f0max, f0min, frame_period):
    audio = input_audio_path2wav[input_audio_path]
    f0, t = pyworld.harvest(
        audio,
        fs=fs,
        f0_ceil=f0max,
        f0_floor=f0min,
        frame_period=frame_period,
    )
    f0 = pyworld.stonemask(audio, f0, t, fs)
    return f0


def change_rms(data1, sr1, data2, sr2, rate):
    rms1 = librosa.feature.rms(
        y=data1, frame_length=sr1 // 2 * 2, hop_length=sr1 // 2
    )  # 每半秒一个点
    rms2 = librosa.feature.rms(y=data2, frame_length=sr2 // 2 * 2, hop_length=sr2 // 2)
    rms1 = torch.from_numpy(rms1)
    rms1 = F.interpolate(rms1.unsqueeze(0), size=data2.shape[0], mode="linear").squeeze()
    rms2 = torch.from_numpy(rms2)
    rms2 = F.interpolate(rms2.unsqueeze(0), size=data2.shape[0], mode="linear").squeeze()
    rms2 = torch.max(rms2, torch.zeros_like(rms2) + 1e-6)
    data2 *= (
        torch.pow(rms1, torch.tensor(1 - rate))
        * torch.pow(rms2, torch.tensor(rate - 1))
    ).numpy()
    return data2


class Pipeline(object):
    def __init__(self, tgt_sr, config):
        # Minimal chunking tweaks: add +0.2s to x_pad, +2s to x_query
        self.x_pad = config.x_pad + 0.2
        self.x_query = config.x_query + 2
        self.x_center = config.x_center
        self.x_max = config.x_max
        self.is_half = config.is_half

        self.sr = 16000
        self.window = 160
        self.t_pad = int(self.sr * self.x_pad)
        self.t_pad_tgt = int(tgt_sr * self.x_pad)
        self.t_pad2 = self.t_pad * 2
        self.t_query = int(self.sr * self.x_query)
        self.t_center = int(self.sr * self.x_center)
        self.t_max = int(self.sr * self.x_max)
        self.device = config.device

    def get_f0(
        self,
        input_audio_path,
        x,
        p_len,
        f0_up_key,
        f0_method,
        filter_radius,
        inp_f0=None,
    ):
        global input_audio_path2wav
        time_step = self.window / self.sr * 1000
        f0_min = 50
        f0_max = 1100
        f0_mel_min = 1127 * np.log(1 + f0_min / 700)
        f0_mel_max = 1127 * np.log(1 + f0_max / 700)

        if f0_method == "pm":
            f0 = (
                parselmouth.Sound(x, self.sr)
                .to_pitch_ac(
                    time_step=time_step / 1000,
                    voicing_threshold=0.6,
                    pitch_floor=f0_min,
                    pitch_ceiling=f0_max,
                )
                .selected_array["frequency"]
            )
            pad_size = (p_len - len(f0) + 1) // 2
            if pad_size > 0 or p_len - len(f0) - pad_size > 0:
                f0 = np.pad(
                    f0, [[pad_size, p_len - len(f0) - pad_size]], mode="constant"
                )
        elif f0_method == "harvest":
            input_audio_path2wav[input_audio_path] = x.astype(np.double)
            f0 = cache_harvest_f0(input_audio_path, self.sr, f0_max, f0_min, 10)
            if filter_radius > 2:
                f0 = signal.medfilt(f0, 3)
        elif f0_method == "crepe":
            model = "full"
            batch_size = 512
            audio_t = torch.tensor(np.copy(x))[None].float()
            f0, pd = torchcrepe.predict(
                audio_t,
                self.sr,
                self.window,
                f0_min,
                f0_max,
                model,
                batch_size=batch_size,
                device=self.device,
                return_periodicity=True,
            )
            pd = torchcrepe.filter.median(pd, 3)
            f0 = torchcrepe.filter.mean(f0, 3)
            f0[pd < 0.1] = 0
            f0 = f0[0].cpu().numpy()
        else:
            if not hasattr(self, "model_rmvpe"):
                from rvc.infer.lib.rmvpe import RMVPE
                rvmpe_model_path = os.path.join(model_path, "rvc", "rmvpe.pt")
                logger.info(f"Loading RMVPE model from {rvmpe_model_path}")
                self.model_rmvpe = RMVPE(
                    model_path=rvmpe_model_path,
                    is_half=self.is_half,
                    device=self.device,
                )
            f0 = self.model_rmvpe.infer_from_audio(x, thred=0.03)

            if "privateuseone" in str(self.device):
                del self.model_rmvpe.model
                del self.model_rmvpe
                logger.info("Cleaning ortruntime memory")

        # pitch shift
        f0 *= pow(2, f0_up_key / 12)
        tf0 = self.sr // self.window

        if inp_f0 is not None:
            delta_t = np.round(
                (inp_f0[:, 0].max() - inp_f0[:, 0].min()) * tf0 + 1
            ).astype("int16")
            replace_f0 = np.interp(list(range(delta_t)), inp_f0[:, 0] * 100, inp_f0[:, 1])
            shape = f0[self.x_pad * tf0 : self.x_pad * tf0 + len(replace_f0)].shape[0]
            f0[self.x_pad * tf0 : self.x_pad * tf0 + len(replace_f0)] = replace_f0[:shape]

        f0bak = f0.copy()
        f0_mel = 1127 * np.log(1 + f0 / 700)
        f0_mel[f0_mel > 0] = (
            (f0_mel[f0_mel > 0] - f0_mel_min) * 254 / (f0_mel_max - f0_mel_min) + 1
        )
        f0_mel[f0_mel <= 1] = 1
        f0_mel[f0_mel > 255] = 255
        f0_coarse = np.rint(f0_mel).astype(np.int32)
        return f0_coarse, f0bak

    def vc(
        self,
        model,
        net_g,
        sid,
        audio0,
        pitch,
        pitchf,
        times,
        index,
        big_npy,
        index_rate,
        version,
        protect,
    ):
        feats = torch.from_numpy(audio0)
        if self.is_half:
            feats = feats.half()
        else:
            feats = feats.float()
        if feats.dim() == 2:  # forced mono in original
            feats = feats.mean(-1)

        assert feats.dim() == 1, feats.dim()
        feats = feats.view(1, -1)
        padding_mask = torch.BoolTensor(feats.shape).to(self.device).fill_(False)

        inputs = {
            "source": feats.to(self.device),
            "padding_mask": padding_mask,
            "output_layer": 9 if version == "v1" else 12,
        }
        t0 = ttime()
        with torch.no_grad():
            logits = model.extract_features(**inputs)
            feats = model.final_proj(logits[0]) if version == "v1" else logits[0]

        if protect < 0.5 and pitch is not None and pitchf is not None:
            feats0 = feats.clone()

        if (
            index is not None
            and big_npy is not None
            and index_rate != 0
        ):
            npy = feats[0].cpu().numpy()
            if self.is_half:
                npy = npy.astype("float32")

            score, ix = index.search(npy, k=8)
            weight = np.square(1 / score)
            weight /= weight.sum(axis=1, keepdims=True)
            npy = np.sum(big_npy[ix] * np.expand_dims(weight, axis=2), axis=1)

            if self.is_half:
                npy = npy.astype("float16")
            feats = (
                torch.from_numpy(npy).unsqueeze(0).to(self.device) * index_rate
                + (1 - index_rate) * feats
            )

        feats = F.interpolate(feats.permute(0, 2, 1), scale_factor=2).permute(0, 2, 1)
        if protect < 0.5 and pitch is not None and pitchf is not None:
            feats0 = F.interpolate(
                feats0.permute(0, 2, 1), scale_factor=2
            ).permute(0, 2, 1)

        t1 = ttime()
        p_len = audio0.shape[0] // self.window
        if feats.shape[1] < p_len:
            p_len = feats.shape[1]
            if pitch is not None and pitchf is not None:
                pitch = pitch[:, :p_len]
                pitchf = pitchf[:, :p_len]

        if protect < 0.5 and pitch is not None and pitchf is not None:
            pitchff = pitchf.clone()
            pitchff[pitchf > 0] = 1
            pitchff[pitchf < 1] = protect
            pitchff = pitchff.unsqueeze(-1)
            feats = feats * pitchff + feats0 * (1 - pitchff)
            feats = feats.to(feats0.dtype)

        p_len = torch.tensor([p_len], device=self.device).long()
        with torch.no_grad():
            has_pitch = pitch is not None and pitchf is not None
            arg = (feats, p_len, pitch, pitchf, sid) if has_pitch else (feats, p_len, sid)
            audio1 = net_g.infer(*arg)[0][0, 0].data.cpu().float().numpy()
        del has_pitch, arg, feats, p_len, padding_mask
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        t2 = ttime()
        times[0] += t1 - t0
        times[2] += t2 - t1
        return audio1

    def _run_pipeline_mono(
        self,
        model,
        net_g,
        sid,
        audio,
        input_audio_path,
        times,
        f0_up_key,
        f0_method,
        file_index,
        index_rate,
        if_f0,
        filter_radius,
        tgt_sr,
        resample_sr,
        rms_mix_rate,
        version,
        protect,
        f0_file,
    ):
        # This is the original chunk logic, unchanged except for being a helper method.
        if file_index != "" and os.path.exists(file_index) and index_rate != 0:
            try:
                index = faiss.read_index(file_index)
                big_npy = index.reconstruct_n(0, index.ntotal)
            except:
                traceback.print_exc()
                index = big_npy = None
        else:
            index = big_npy = None

        audio = signal.filtfilt(bh, ah, audio)
        audio_pad = np.pad(audio, (self.window // 2, self.window // 2), mode="reflect")
        opt_ts = []
        if audio_pad.shape[0] > self.t_max:
            audio_sum = np.zeros_like(audio)
            for i in range(self.window):
                audio_sum += np.abs(audio_pad[i : i - self.window])
            for t in range(self.t_center, audio.shape[0], self.t_center):
                opt_ts.append(
                    t
                    - self.t_query
                    + np.where(
                        audio_sum[t - self.t_query : t + self.t_query]
                        == audio_sum[t - self.t_query : t + self.t_query].min()
                    )[0][0]
                )
        s = 0
        audio_opt = []
        t1 = ttime()
        audio_pad = np.pad(audio, (self.t_pad, self.t_pad), mode="reflect")
        p_len = audio_pad.shape[0] // self.window
        inp_f0 = None
        if hasattr(f0_file, "name"):
            try:
                with open(f0_file.name, "r") as f:
                    lines = f.read().strip("\n").split("\n")
                inp_f0 = []
                for line in lines:
                    inp_f0.append([float(i) for i in line.split(",")])
                inp_f0 = np.array(inp_f0, dtype="float32")
            except:
                traceback.print_exc()
        sid_tensor = torch.tensor(sid, device=self.device).unsqueeze(0).long()
        pitch, pitchf = None, None
        if if_f0 == 1:
            pitch, pitchf = self.get_f0(
                input_audio_path,
                audio_pad,
                p_len,
                f0_up_key,
                f0_method,
                filter_radius,
                inp_f0,
            )
            pitch = pitch[:p_len]
            pitchf = pitchf[:p_len]
            if "mps" not in str(self.device) or "xpu" not in str(self.device):
                pitchf = pitchf.astype(np.float32)
            pitch = torch.tensor(pitch, device=self.device).unsqueeze(0).long()
            pitchf = torch.tensor(pitchf, device=self.device).unsqueeze(0).float()
        t2 = ttime()
        times[1] += t2 - t1

        for t in opt_ts:
            t = t // self.window * self.window
            if if_f0 == 1:
                seg = self.vc(
                    model,
                    net_g,
                    sid_tensor,
                    audio_pad[s : t + self.t_pad2 + self.window],
                    pitch[:, s // self.window : (t + self.t_pad2) // self.window],
                    pitchf[:, s // self.window : (t + self.t_pad2) // self.window],
                    times,
                    index,
                    big_npy,
                    index_rate,
                    version,
                    protect,
                )
                seg = seg[self.t_pad_tgt : -self.t_pad_tgt]
            else:
                seg = self.vc(
                    model,
                    net_g,
                    sid_tensor,
                    audio_pad[s : t + self.t_pad2 + self.window],
                    None,
                    None,
                    times,
                    index,
                    big_npy,
                    index_rate,
                    version,
                    protect,
                )
                seg = seg[self.t_pad_tgt : -self.t_pad_tgt]
            audio_opt.append(seg)
            s = t

        # Final chunk
        if if_f0 == 1:
            final_seg = self.vc(
                model,
                net_g,
                sid_tensor,
                audio_pad[s:],
                pitch[:, s // self.window :] if s else pitch,
                pitchf[:, s // self.window :] if s else pitchf,
                times,
                index,
                big_npy,
                index_rate,
                version,
                protect,
            )
            final_seg = final_seg[self.t_pad_tgt : -self.t_pad_tgt]
        else:
            final_seg = self.vc(
                model,
                net_g,
                sid_tensor,
                audio_pad[s:],
                None,
                None,
                times,
                index,
                big_npy,
                index_rate,
                version,
                protect,
            )
            final_seg = final_seg[self.t_pad_tgt : -self.t_pad_tgt]
        audio_opt.append(final_seg)

        audio_opt = np.concatenate(audio_opt)
        if rms_mix_rate != 1:
            audio_opt = change_rms(audio, 16000, audio_opt, tgt_sr, rms_mix_rate)

        if tgt_sr != resample_sr >= 16000:
            audio_opt = librosa.resample(audio_opt, orig_sr=tgt_sr, target_sr=resample_sr)

        audio_max = np.abs(audio_opt).max() / 0.99
        max_int16 = 32768
        if audio_max > 1:
            max_int16 /= audio_max
        audio_opt = (audio_opt * max_int16).astype(np.int16)

        del pitch, pitchf, sid_tensor
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return audio_opt

    def pipeline(
        self,
        model,
        net_g,
        sid,
        audio,
        input_audio_path,
        times,
        f0_up_key,
        f0_method,
        file_index,
        index_rate,
        if_f0,
        filter_radius,
        tgt_sr,
        resample_sr,
        rms_mix_rate,
        version,
        protect,
        f0_file=None,
    ):
        """
        If 'audio' is stereo, split into left/right, run the same logic for each,
        then re-stack the outputs. If mono, run as before.
        """
        # Check if 'audio' is stereo
        if audio.ndim == 2:
            # Suppose shape is (samples, 2)
            # If shape is (2, samples), transpose it to (samples, 2)
            if audio.shape[0] == 2 and audio.shape[0] < audio.shape[1]:
                audio = audio.T

            if audio.shape[1] == 2:
                # split into left, right
                left = audio[:, 0]
                right = audio[:, 1]

                left_opt = self._run_pipeline_mono(
                    model,
                    net_g,
                    sid,
                    left,
                    input_audio_path,
                    times,
                    f0_up_key,
                    f0_method,
                    file_index,
                    index_rate,
                    if_f0,
                    filter_radius,
                    tgt_sr,
                    resample_sr,
                    rms_mix_rate,
                    version,
                    protect,
                    f0_file,
                )
                right_opt = self._run_pipeline_mono(
                    model,
                    net_g,
                    sid,
                    right,
                    input_audio_path,
                    times,
                    f0_up_key,
                    f0_method,
                    file_index,
                    index_rate,
                    if_f0,
                    filter_radius,
                    tgt_sr,
                    resample_sr,
                    rms_mix_rate,
                    version,
                    protect,
                    f0_file,
                )
                # align lengths
                min_len = min(len(left_opt), len(right_opt))
                left_opt = left_opt[:min_len]
                right_opt = right_opt[:min_len]

                # stack as stereo
                return np.stack([left_opt, right_opt], axis=1)
            else:
                # fallback: treat as mono if shape isn't strictly (samples, 2)
                return self._run_pipeline_mono(
                    model,
                    net_g,
                    sid,
                    audio.flatten(),
                    input_audio_path,
                    times,
                    f0_up_key,
                    f0_method,
                    file_index,
                    index_rate,
                    if_f0,
                    filter_radius,
                    tgt_sr,
                    resample_sr,
                    rms_mix_rate,
                    version,
                    protect,
                    f0_file,
                )
        else:
            # mono path (original)
            return self._run_pipeline_mono(
                model,
                net_g,
                sid,
                audio,
                input_audio_path,
                times,
                f0_up_key,
                f0_method,
                file_index,
                index_rate,
                if_f0,
                filter_radius,
                tgt_sr,
                resample_sr,
                rms_mix_rate,
                version,
                protect,
                f0_file,
            )

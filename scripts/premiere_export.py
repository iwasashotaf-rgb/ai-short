"""工程4: Premiere Pro に渡すファイルを書き出す。

- <名前>.xml        … FCP7 XML（xmeml v4）。9:16 シーケンスにカット済みのクリップを並べる。
                       クリップは元素材を参照するので、Premiere 上でカット点を前後に伸ばせる。
- <名前>_captions.srt … 字幕。Premiere で読み込むとキャプショントラックになり、文言・時刻・書式を編集できる。
- <名前>_title.srt    … 上部タイトル。字幕とは別のキャプショントラックにして位置と書式を分けられるようにする。

タイムラインの3つの形に対応する:
- A 収録音声型（timeline.py）: 素材1本。映像と音声は同じ素材から切り、リンクする
- B 字コンテ型（build_b.py）: 素材が複数（tl["sources"]）。音声はナレーション（tl["audio"]）
- C インタビュー型（timeline_c.py）: 素材が複数。音声は収録した話し声で、カットごとに1本。
  インサートで映像だけ差し替えたカットは、映像と音声をリンクしない
"""
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape


def _rate(tl):
    s = tl["source"]
    ntsc = s["fps_den"] == 1001
    timebase = round(s["fps_num"] / s["fps_den"])
    return f"<rate><timebase>{timebase}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


def _pathurl(p):
    return "file://localhost" + quote(str(Path(p).resolve()))


def _motion(scale, horiz=0.0, vert=0.0):
    return f"""
          <filter>
            <effect>
              <name>Basic Motion</name>
              <effectid>basic</effectid>
              <effectcategory>motion</effectcategory>
              <effecttype>motion</effecttype>
              <mediatype>video</mediatype>
              <parameter authoringApp="PremierePro">
                <parameterid>scale</parameterid>
                <name>Scale</name>
                <valuemin>0</valuemin>
                <valuemax>1000</valuemax>
                <value>{scale:.4f}</value>
              </parameter>
              <parameter authoringApp="PremierePro">
                <parameterid>center</parameterid>
                <name>Center</name>
                <value>
                  <horiz>{horiz:.6f}</horiz>
                  <vert>{vert:.6f}</vert>
                </value>
              </parameter>
            </effect>
          </filter>"""


def _normalize(tl):
    """A 型のタイムラインを、B 型と同じ「素材の一覧＋映像クリップ＋音声クリップ」の形にそろえる。"""
    if "sources" in tl:
        return tl["sources"], tl["clips"], tl["audio"]
    cx = float(tl["reframe"].get("center_x", 0.5))
    clips = [{**c, "src": 0, "center_x": cx} for c in tl["clips"]]
    audio = []
    if tl["source"]["audio_channels"]:
        audio = [{"src": 0, "src_in": c["src_in"], "src_out": c["src_out"], "rec_in": c["rec_in"],
                  "rec_out": c["rec_out"], "link": c["i"]} for c in tl["clips"]]
    return [tl["source"]], clips, audio


class _Files:
    """素材ファイルの <file> 要素。2回目以降は id だけで参照する（xmeml の決まり）。"""

    def __init__(self, tl, sources):
        self.tl, self.sources, self.done = tl, sources, set()

    def block(self, k):
        fid = f"file-{k + 1}"
        if k in self.done:
            return f'<file id="{fid}"/>'
        self.done.add(k)
        s, tl = self.sources[k], self.tl
        total = int(round(s["duration"] * tl["fps"]))
        video = ""
        if s.get("width"):
            video = f"""
              <video>
                <samplecharacteristics>
                  {_rate(tl)}
                  <width>{s['width']}</width>
                  <height>{s['height']}</height>
                  <anamorphic>FALSE</anamorphic>
                  <pixelaspectratio>square</pixelaspectratio>
                  <fielddominance>none</fielddominance>
                </samplecharacteristics>
              </video>"""
        audio = ""
        if s["audio_channels"]:
            # audiochannel が無いと、Premiere は音声クリップを1本も読み込まない（2026-11-05 実機で確認）
            chans = "".join(f"""
                <audiochannel><sourcechannel>{c}</sourcechannel></audiochannel>"""
                            for c in range(1, s["audio_channels"] + 1))
            audio = f"""
              <audio>
                <samplecharacteristics>
                  <depth>16</depth>
                  <samplerate>{s['audio_rate']}</samplerate>
                </samplecharacteristics>
                <channelcount>{s['audio_channels']}</channelcount>{chans}
              </audio>"""
        return f"""<file id="{fid}">
            <name>{escape(Path(s['path']).name)}</name>
            <pathurl>{escape(_pathurl(s['path']))}</pathurl>
            {_rate(tl)}
            <duration>{total}</duration>
            <timecode>{_rate(tl)}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>
            <media>{video}{audio}
            </media>
          </file>"""


def _layouts(tl, s, center_x, zoom=1.0, center_y=0.5):
    """リフレームを Premiere のモーション（スケールと位置）に置き換える。

    戻り値は映像トラックごとの (スケール%, 位置の横オフセット, 縦オフセット)。
    位置は xmeml の Center 値で、0 が画面中央。
    """
    fill = max(tl["out_w"] / s["width"], tl["out_h"] / s["height"]) * 100
    if tl["reframe"]["mode"] == "crop":
        fill *= zoom  # 寄せ（cutlist の zoom）
        scaled_w = s["width"] * fill / 100
        scaled_h = s["height"] * fill / 100
        # 切り抜き枠の中心を素材の center_x / center_y の位置に合わせるための移動量（ピクセル）
        shift_px = (0.5 - center_x) * (scaled_w - tl["out_w"])
        shift_py = (0.5 - center_y) * (scaled_h - tl["out_h"])
        # Premiere 26 は xmeml の Center の値を2倍して読む。0.078 と書くと、位置が 0.5→0.656
        # （画面の高さの15.6%）に動いた（2026-11-09 に確かめた）。そのため割合を2で割って書く。正の値は右・下
        return [(fill, shift_px / tl["out_w"] / 2, shift_py / tl["out_h"] / 2)]
    fit = tl["out_w"] / s["width"] * 100
    return [(fill, 0.0, 0.0), (fit, 0.0, 0.0)]  # V1: 背景（Premiere でぼかしを足す）/ V2: 全体


def write_xml(tl, path, name):
    sources, clips, audio = _normalize(tl)
    files = _Files(tl, sources)
    n_vtracks = 1 if tl["reframe"]["mode"] == "crop" else 2
    linked = {a["link"]: j for j, a in enumerate(audio) if a.get("link") is not None}

    def src_frames(k):
        return int(round(sources[k]["duration"] * tl["fps"]))

    vtracks = []
    for ti in range(n_vtracks):
        items = []
        for ci, c in enumerate(clips):
            s = sources[c["src"]]
            scale, horiz, vert = _layouts(tl, s, float(c.get("center_x", 0.5)),
                                          float(c.get("zoom", 1.0)), float(c.get("center_y", 0.5)))[ti]
            vid = f"clipitem-v{ti + 1}-{ci + 1}"
            links = ""
            if ti == 0 and c["i"] in linked:
                j = linked[c["i"]]
                links = f"""
          <link><linkclipref>{vid}</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>{ci + 1}</clipindex></link>
          <link><linkclipref>clipitem-a1-{j + 1}</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>{j + 1}</clipindex></link>"""
            items.append(f"""
        <clipitem id="{vid}">
          <masterclipid>masterclip-{c['src'] + 1}</masterclipid>
          <name>{escape(Path(s['path']).name)}</name>
          <enabled>TRUE</enabled>
          <duration>{src_frames(c['src'])}</duration>
          {_rate(tl)}
          <start>{c['rec_in']}</start>
          <end>{c['rec_out']}</end>
          <in>{c['src_in']}</in>
          <out>{c['src_out']}</out>
          {files.block(c['src'])}{_motion(scale, horiz, vert)}{links}
        </clipitem>""")
        vtracks.append("<track>" + "".join(items) + "\n      </track>")

    atracks = ""
    if audio:
        items = []
        for j, a in enumerate(audio):
            s = sources[a["src"]]
            aid = f"clipitem-a1-{j + 1}"
            links = ""
            if a.get("link") is not None:
                ci = next(k for k, c in enumerate(clips) if c["i"] == a["link"])
                links = f"""
          <link><linkclipref>clipitem-v1-{ci + 1}</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>{ci + 1}</clipindex></link>
          <link><linkclipref>{aid}</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>{j + 1}</clipindex></link>"""
            items.append(f"""
        <clipitem id="{aid}" premiereChannelType="{'stereo' if s['audio_channels'] >= 2 else 'mono'}">
          <masterclipid>masterclip-{a['src'] + 1}</masterclipid>
          <name>{escape(Path(s['path']).name)}</name>
          <enabled>TRUE</enabled>
          <duration>{src_frames(a['src'])}</duration>
          {_rate(tl)}
          <start>{a['rec_in']}</start>
          <end>{a['rec_out']}</end>
          <in>{a['src_in']}</in>
          <out>{a['src_out']}</out>
          {files.block(a['src'])}
          <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>{links}
        </clipitem>""")
        ch = sources[audio[0]["src"]]["audio_channels"]
        track_type = "Stereo" if ch >= 2 else "Mono"
        rate = sources[audio[0]["src"]]["audio_rate"]
        # トラックの属性（currentExplodedTrackIndex など）が無いと、Premiere 2026 は
        # 音声クリップを1本も読み込まない。Premiere 自身の書き出しから写した（2026-11-05 実機で確認）
        atracks = f"""
      <audio>
        <numOutputChannels>2</numOutputChannels>
        <format><samplecharacteristics><depth>16</depth><samplerate>{rate}</samplerate></samplecharacteristics></format>
        <outputs>
          <group><index>1</index><numchannels>1</numchannels><downmix>0</downmix><channel><index>1</index></channel></group>
          <group><index>2</index><numchannels>1</numchannels><downmix>0</downmix><channel><index>2</index></channel></group>
        </outputs>
        <track TL.SQTrackAudioKeyframeStyle="0" TL.SQTrackShy="0" TL.SQTrackExpandedHeight="41" TL.SQTrackExpanded="0" MZ.TrackTargeted="1" PannerCurrentValue="0.5" PannerIsInverted="true" currentExplodedTrackIndex="0" totalExplodedTrackCount="1" premiereTrackType="{track_type}">{''.join(items)}
        </track>
      </audio>"""

    markers = "".join(f"""
    <marker><comment>{escape(m['comment'][:120])}</comment><name>{escape(m['name'])}</name><in>{m['rec_in']}</in><out>-1</out></marker>"""
                      for m in tl.get("markers") or
                      [{"comment": c["text"], "name": c["note"] or f"カット{c['i'] + 1}", "rec_in": c["rec_in"]}
                       for c in tl["clips"]])

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence id="sequence-1">
    <name>{escape(name)}</name>
    <duration>{tl['total_frames']}</duration>
    {_rate(tl)}
    <timecode>{_rate(tl)}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>{markers}
    <media>
      <video>
        <format>
          <samplecharacteristics>
            {_rate(tl)}
            <width>{tl['out_w']}</width>
            <height>{tl['out_h']}</height>
            <anamorphic>FALSE</anamorphic>
            <pixelaspectratio>square</pixelaspectratio>
            <fielddominance>none</fielddominance>
          </samplecharacteristics>
        </format>
      {chr(10).join(vtracks)}
      </video>{atracks}
    </media>
  </sequence>
</xmeml>
"""
    Path(path).write_text(xml, encoding="utf-8")
    return len(clips)


def _srt_time(frames, fps):
    ms = int(round(frames / fps * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues, fps, path):
    out = []
    for i, (f0, f1, text) in enumerate(cues, start=1):
        out.append(f"{i}\n{_srt_time(f0, fps)} --> {_srt_time(f1, fps)}\n{text}\n")
    Path(path).write_text("\n".join(out), encoding="utf-8")


def export(tl, out_dir, name, seq_name=None):
    """name はファイル名、seq_name は Premiere 上のシーケンス名（省略時は name）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = [out / f"{name}.xml"]
    write_xml(tl, files[0], seq_name or name)
    if tl["captions"]:
        p = out / f"{name}_captions.srt"
        write_srt([(c["rec_in"], c["rec_out"], c["text"]) for c in tl["captions"]], tl["fps"], p)
        files.append(p)
    # 上部テロップ: C 型は質問ごと（tl["titles"]）、A・B 型は全編に出す1枚（tl["title"]）
    titles = [(t["rec_in"], t["rec_out"], t["text"]) for t in (tl.get("titles") or [])]
    if not titles and tl["title"]:
        titles = [(0, tl["total_frames"], tl["title"])]
    if titles:
        p = out / f"{name}_title.srt"
        write_srt(titles, tl["fps"], p)
        files.append(p)
    return files

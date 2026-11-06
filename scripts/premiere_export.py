"""工程4: Premiere Pro に渡すファイルを書き出す。

- <名前>.xml        … FCP7 XML（xmeml v4）。9:16 シーケンスにカット済みのクリップを並べる。
                       クリップは元素材を参照するので、Premiere 上でカット点を前後に伸ばせる。
- <名前>_captions.srt … 字幕。Premiere で読み込むとキャプショントラックになり、文言・時刻・書式を編集できる。
- <名前>_title.srt    … 上部タイトル。字幕とは別のキャプショントラックにして位置と書式を分けられるようにする。
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


def _file_block(tl, first):
    s = tl["source"]
    if not first:
        return '<file id="file-1"/>'
    total = int(round(s["duration"] * s["fps"]))
    audio = ""
    if s["audio_channels"]:
        # audiochannel が無いと、Premiere は音声クリップを1本も読み込まない（2026-11-05 実機で確認）
        chans = "".join(f"""
                <audiochannel><sourcechannel>{k}</sourcechannel></audiochannel>"""
                        for k in range(1, s["audio_channels"] + 1))
        audio = f"""
              <audio>
                <samplecharacteristics>
                  <depth>16</depth>
                  <samplerate>{s['audio_rate']}</samplerate>
                </samplecharacteristics>
                <channelcount>{s['audio_channels']}</channelcount>{chans}
              </audio>"""
    return f"""<file id="file-1">
            <name>{escape(Path(s['path']).name)}</name>
            <pathurl>{escape(_pathurl(s['path']))}</pathurl>
            {_rate(tl)}
            <duration>{total}</duration>
            <timecode>{_rate(tl)}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>
            <media>
              <video>
                <samplecharacteristics>
                  {_rate(tl)}
                  <width>{s['width']}</width>
                  <height>{s['height']}</height>
                  <anamorphic>FALSE</anamorphic>
                  <pixelaspectratio>square</pixelaspectratio>
                  <fielddominance>none</fielddominance>
                </samplecharacteristics>
              </video>{audio}
            </media>
          </file>"""


def _layout(tl):
    """リフレームを Premiere のモーション（スケールと位置）に置き換える。

    戻り値は映像トラックごとの (スケール%, 位置の横オフセット)。
    位置は xmeml の Center 値で、0 が画面中央。横方向の値はシーケンス幅に対する割合として書く。
    """
    s = tl["source"]
    fill = max(tl["out_w"] / s["width"], tl["out_h"] / s["height"]) * 100
    if tl["reframe"]["mode"] == "crop":
        cx = float(tl["reframe"].get("center_x", 0.5))
        scaled_w = s["width"] * fill / 100
        # 切り抜き枠の中心を素材の cx の位置に合わせるための横移動量（ピクセル）
        shift_px = (0.5 - cx) * (scaled_w - tl["out_w"])
        return [(fill, shift_px / tl["out_w"])]
    fit = tl["out_w"] / s["width"] * 100
    return [(fill, 0.0), (fit, 0.0)]  # V1: 背景（Premiere でぼかしを足す）/ V2: 全体


def write_xml(tl, path, name):
    s = tl["source"]
    total_src = int(round(s["duration"] * s["fps"]))
    layout = _layout(tl)
    n = len(tl["clips"])

    vtracks = []
    first = True
    for ti, (scale, horiz) in enumerate(layout):
        items = []
        for c in tl["clips"]:
            vid = f"clipitem-v{ti + 1}-{c['i'] + 1}"
            links = ""
            if ti == 0:
                links = f"""
          <link><linkclipref>{vid}</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>{c['i'] + 1}</clipindex></link>"""
                if s["audio_channels"]:
                    links += f"""
          <link><linkclipref>clipitem-a1-{c['i'] + 1}</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>{c['i'] + 1}</clipindex></link>"""
            items.append(f"""
        <clipitem id="{vid}">
          <masterclipid>masterclip-1</masterclipid>
          <name>{escape(Path(s['path']).name)}</name>
          <enabled>TRUE</enabled>
          <duration>{total_src}</duration>
          {_rate(tl)}
          <start>{c['rec_in']}</start>
          <end>{c['rec_out']}</end>
          <in>{c['src_in']}</in>
          <out>{c['src_out']}</out>
          {_file_block(tl, first)}{_motion(scale, horiz)}{links}
        </clipitem>""")
            first = False
        vtracks.append("<track>" + "".join(items) + "\n      </track>")

    atracks = ""
    if s["audio_channels"]:
        items = []
        for c in tl["clips"]:
            aid = f"clipitem-a1-{c['i'] + 1}"
            items.append(f"""
        <clipitem id="{aid}" premiereChannelType="{'stereo' if s['audio_channels'] >= 2 else 'mono'}">
          <masterclipid>masterclip-1</masterclipid>
          <name>{escape(Path(s['path']).name)}</name>
          <enabled>TRUE</enabled>
          <duration>{total_src}</duration>
          {_rate(tl)}
          <start>{c['rec_in']}</start>
          <end>{c['rec_out']}</end>
          <in>{c['src_in']}</in>
          <out>{c['src_out']}</out>
          <file id="file-1"/>
          <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>
          <link><linkclipref>clipitem-v1-{c['i'] + 1}</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>{c['i'] + 1}</clipindex></link>
          <link><linkclipref>{aid}</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>{c['i'] + 1}</clipindex></link>
        </clipitem>""")
        track_type = "Stereo" if s["audio_channels"] >= 2 else "Mono"
        # トラックの属性（currentExplodedTrackIndex など）が無いと、Premiere 2026 は
        # 音声クリップを1本も読み込まない。Premiere 自身の書き出しから写した（2026-11-05 実機で確認）
        atracks = f"""
      <audio>
        <numOutputChannels>2</numOutputChannels>
        <format><samplecharacteristics><depth>16</depth><samplerate>{s['audio_rate']}</samplerate></samplecharacteristics></format>
        <outputs>
          <group><index>1</index><numchannels>1</numchannels><downmix>0</downmix><channel><index>1</index></channel></group>
          <group><index>2</index><numchannels>1</numchannels><downmix>0</downmix><channel><index>2</index></channel></group>
        </outputs>
        <track TL.SQTrackAudioKeyframeStyle="0" TL.SQTrackShy="0" TL.SQTrackExpandedHeight="41" TL.SQTrackExpanded="0" MZ.TrackTargeted="1" PannerCurrentValue="0.5" PannerIsInverted="true" currentExplodedTrackIndex="0" totalExplodedTrackCount="1" premiereTrackType="{track_type}">{''.join(items)}
        </track>
      </audio>"""

    markers = "".join(f"""
    <marker><comment>{escape(c['text'][:120])}</comment><name>{escape(c['note'] or f'カット{c["i"] + 1}')}</name><in>{c['rec_in']}</in><out>-1</out></marker>"""
                      for c in tl["clips"])

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
    return n


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


def export(tl, out_dir, name):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = [out / f"{name}.xml"]
    write_xml(tl, files[0], name)
    if tl["captions"]:
        p = out / f"{name}_captions.srt"
        write_srt([(c["rec_in"], c["rec_out"], c["text"]) for c in tl["captions"]], tl["fps"], p)
        files.append(p)
    if tl["title"]:
        p = out / f"{name}_title.srt"
        write_srt([(0, tl["total_frames"], tl["title"])], tl["fps"], p)
        files.append(p)
    return files

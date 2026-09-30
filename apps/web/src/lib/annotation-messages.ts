// Zoomable spectrogram controls, shared by every listening screen.
export const annotationEn = {
  aZoomShort:'Drag to move · Double-click to play from there · Scroll to zoom', aZoomHint: 'Drag (or use the arrow keys) to move the view. Double-click to move the play position and start playback there. Scroll to zoom; use the axes to zoom time and frequency separately.', aZoomIn:'Zoom in', aZoomOut:'Zoom out', aFit: 'Show whole clip', aFrequencyAxis: 'Frequency (kHz)', aTimeAxis: 'Time (seconds)',
  aAxisHelp: 'Scroll or use the + and − keys to zoom this axis.',
  annotation_changed: 'This sound was answered elsewhere. Reload it before changing the answer.',
};
export const annotationJa: Record<keyof typeof annotationEn,string> = {
  aZoomShort:'ドラッグで移動・ダブルクリックでそこから再生・スクロールで拡大', aZoomHint: 'ドラッグ（または矢印キー）で表示を動かします。ダブルクリックで再生位置を移し、そこから再生します。スクロールで拡大・縮小。時間軸なら横だけ、周波数軸なら縦だけ変えられます。', aZoomIn:'拡大', aZoomOut:'縮小', aFit: '全体に戻す', aFrequencyAxis: '周波数（kHz）', aTimeAxis: '時間（秒）',
  aAxisHelp: 'スクロール、または＋・−キーでこの軸を拡大・縮小できます。',
  annotation_changed: 'この音は別の画面で答えが保存されています。読み込み直してから変えてください。',
};

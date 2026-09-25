import { useEffect, useMemo, useState } from 'react'
import { OverlayPortal } from './OverlayPortal'

export interface PreviewFileSource {
  name: string
  mediaType: string
  sizeBytes: number
  file?: File
  remoteUrl?: string
  downloadUrl?: string
  description?: string
}

interface FilePreviewDialogProps {
  source: PreviewFileSource
  onClose: () => void
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

function isTextFile(source: PreviewFileSource) {
  const name = source.name.toLowerCase()
  return source.mediaType.startsWith('text/') || name.endsWith('.md') || name.endsWith('.txt')
}

export function FilePreviewDialog({ source, onClose }: FilePreviewDialogProps) {
  const [textContent, setTextContent] = useState('')
  const [textError, setTextError] = useState('')
  const objectUrl = useMemo(
    () => source.file ? URL.createObjectURL(source.file) : source.remoteUrl ?? null,
    [source.file, source.remoteUrl],
  )

  useEffect(() => {
    return () => {
      if (source.file && objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [objectUrl, source.file])

  useEffect(() => {
    let cancelled = false
    if ((!source.file && !source.remoteUrl) || !isTextFile(source)) {
      return
    }
    const read = source.file
      ? source.file.text()
      : fetch(source.remoteUrl!).then((response) => {
        if (!response.ok) throw new Error('file response failed')
        return response.text()
      })
    read
      .then((content) => {
        if (!cancelled) setTextContent(content)
      })
      .catch(() => {
        if (!cancelled) setTextError('文本内容读取失败。')
      })
    return () => {
      cancelled = true
    }
  }, [source])

  const isPdf = source.mediaType === 'application/pdf' || source.name.toLowerCase().endsWith('.pdf')

  return (
    <OverlayPortal>
      <div className="file-preview-layer" role="presentation">
        <button className="file-preview-backdrop" type="button" aria-label="关闭文件预览" onClick={onClose} />
        <section className="file-preview-dialog" role="dialog" aria-modal="true" aria-label={`${source.name} 文件预览`}>
        <header>
          <div>
            <p className="eyebrow">FILE PREVIEW</p>
            <h2>{source.name}</h2>
            <span>{source.mediaType || '未知格式'} · {formatBytes(source.sizeBytes)}</span>
          </div>
          <button className="drawer-close" type="button" aria-label="关闭预览" onClick={onClose}>×</button>
        </header>

        <div className="file-preview-body">
          {(source.file || source.remoteUrl) && isPdf && objectUrl && (
            <iframe title={`${source.name} PDF 预览`} src={objectUrl} />
          )}
          {(source.file || source.remoteUrl) && isTextFile(source) && (
            <pre className="text-file-preview">{textError || textContent || '正在读取文本内容…'}</pre>
          )}
          {(source.file || source.remoteUrl) && !isPdf && !isTextFile(source) && (
            <div className="file-preview-placeholder">
              <strong>该格式暂不支持浏览器内预览</strong>
              <p>文件已经选中，仍可继续上传。DOCX 等格式需要后端转换为 PDF 或 HTML 后才能完整预览。</p>
            </div>
          )}
          {!source.file && !source.remoteUrl && (
            <div className="file-preview-placeholder">
              <strong>文件内容流接口尚未接入</strong>
              <p>{source.description ?? '当前可以查看文件名、格式、版本和哈希；接入受控文件下载接口后即可在此展示原文。'}</p>
            </div>
          )}
        </div>
        {source.downloadUrl && <footer className="inline-actions"><a className="button button-submit" href={source.downloadUrl}>下载原件</a></footer>}
        </section>
      </div>
    </OverlayPortal>
  )
}

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
        if (!cancelled) setTextError('Unable to load the text content.')
      })
    return () => {
      cancelled = true
    }
  }, [source])

  const isPdf = source.mediaType === 'application/pdf' || source.name.toLowerCase().endsWith('.pdf')

  return (
    <OverlayPortal>
      <div className="file-preview-layer" role="presentation">
        <button className="file-preview-backdrop" type="button" aria-label="CloseFile preview" onClick={onClose} />
        <section className="file-preview-dialog" role="dialog" aria-modal="true" aria-label={`${source.name} File preview`}>
        <header>
          <div>
            <p className="eyebrow">FILE PREVIEW</p>
            <h2>{source.name}</h2>
            <span>{source.mediaType || 'Unknown format'} · {formatBytes(source.sizeBytes)}</span>
          </div>
          <button className="drawer-close" type="button" aria-label="ClosePreview" onClick={onClose}>×</button>
        </header>

        <div className="file-preview-body">
          {(source.file || source.remoteUrl) && isPdf && objectUrl && (
            <iframe title={`${source.name} PDF Preview`} src={objectUrl} />
          )}
          {(source.file || source.remoteUrl) && isTextFile(source) && (
            <pre className="text-file-preview">{textError || textContent || 'Loading text content…'}</pre>
          )}
          {(source.file || source.remoteUrl) && !isPdf && !isTextFile(source) && (
            <div className="file-preview-placeholder">
              <strong>This format cannot be previewed in the browser.</strong>
              <p>The file has been selected and can still be uploaded. Formats such as DOCX require backend conversion to PDF or HTML for full preview.</p>
            </div>
          )}
          {!source.file && !source.remoteUrl && (
            <div className="file-preview-placeholder">
              <strong>The file content endpoint is not available.</strong>
              <p>{source.description ?? 'The file name, format, version and hash are available. Source content will appear here when controlled file access is enabled.'}</p>
            </div>
          )}
        </div>
        {source.downloadUrl && <footer className="inline-actions"><a className="button button-submit" href={source.downloadUrl}>Download source</a></footer>}
        </section>
      </div>
    </OverlayPortal>
  )
}

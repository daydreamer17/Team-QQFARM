import { type FormEvent, useRef, useState } from 'react'
import { FilePreviewDialog, type PreviewFileSource } from '../components/FilePreviewDialog'

type ResourceCategory = '采购政策' | '评审规则' | '技术规范' | '合同条款'

interface LocalResource {
  id: string
  file: File
  category: ResourceCategory
  addedAt: string
}

const acceptedExtensions = ['pdf', 'md', 'txt', 'csv', 'docx']

function fileExtension(name: string) {
  return name.includes('.') ? name.split('.').pop()?.toUpperCase() ?? 'FILE' : 'FILE'
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

export function ResourcePage() {
  const fileInput = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [category, setCategory] = useState<ResourceCategory>('采购政策')
  const [resources, setResources] = useState<LocalResource[]>([])
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<PreviewFileSource | null>(null)

  function handleFiles(selected: FileList | null) {
    setError('')
    if (!selected) return
    const next = Array.from(selected)
    const invalid = next.find((file) => !acceptedExtensions.includes(file.name.split('.').pop()?.toLowerCase() ?? ''))
    if (invalid) {
      setFiles([])
      setError(`${invalid.name} 的格式不支持。请上传 PDF、Markdown、TXT、CSV 或 DOCX。`)
      return
    }
    setFiles(next)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (files.length === 0) {
      setError('请至少选择一个规则文件。')
      return
    }
    const timestamp = new Date().toISOString()
    setResources((current) => [
      ...files.map((file) => ({ id: crypto.randomUUID(), file, category, addedAt: timestamp })),
      ...current,
    ])
    setFiles([])
    if (fileInput.current) fileInput.current.value = ''
  }

  return (
    <div className="page-stack resource-page">
      <section className="page-heading resource-heading">
        <div>
          <p className="eyebrow">KNOWLEDGE RESOURCES</p>
          <h1>规则资源库</h1>
          <p>集中管理采购政策、评审规则和技术规范，作为后续 RAG 检索与解释的依据。</p>
        </div>
        <span className="prototype-badge">前端原型 · RAG 接口待接</span>
      </section>

      <section className="resource-summary" aria-label="资源库概况">
        <article><span>当前文件</span><strong>{resources.length}</strong><small>本次浏览器会话</small></article>
        <article><span>支持格式</span><strong>5</strong><small>PDF / MD / TXT / CSV / DOCX</small></article>
        <article><span>索引状态</span><strong>待接入</strong><small>需要后端解析、切分和向量化接口</small></article>
      </section>

      <section className="resource-layout">
        <form className="card resource-upload" onSubmit={handleSubmit}>
          <div>
            <p className="eyebrow">ADD RESOURCES</p>
            <h2>上传规则文件</h2>
            <p>文件会先进入待入库区，后端完成安全检查、解析和索引后才能被 Agent 检索。</p>
          </div>

          <label className="field">
            <span>资源类型</span>
            <select value={category} onChange={(event) => setCategory(event.target.value as ResourceCategory)}>
              <option>采购政策</option>
              <option>评审规则</option>
              <option>技术规范</option>
              <option>合同条款</option>
            </select>
          </label>

          <label className="resource-dropzone">
            <span className="resource-dropzone-icon" aria-hidden="true">↑</span>
            <strong>选择或拖入规则文件</strong>
            <small>支持多个文件；原型阶段单文件建议不超过 10 MiB</small>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept=".pdf,.md,.txt,.csv,.docx,application/pdf,text/markdown,text/plain,text/csv"
              onChange={(event) => handleFiles(event.target.files)}
            />
          </label>

          {files.length > 0 && (
            <div className="resource-pending-files">
              {files.map((file) => (
                <div key={`${file.name}-${file.lastModified}`}>
                  <span><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></span>
                  <button
                    type="button"
                    onClick={() => setPreview({ name: file.name, mediaType: file.type, sizeBytes: file.size, file })}
                  >预览</button>
                </div>
              ))}
            </div>
          )}

          {error && <div className="form-error compact-error" role="alert">{error}</div>}

          <button className="button button-submit" type="submit">加入待入库区</button>
        </form>

        <aside className="card resource-guidance">
          <p className="eyebrow">RAG PIPELINE</p>
          <h2>计划中的入库流程</h2>
          <ol>
            <li><strong>文件检查</strong><span>格式、大小、哈希与权限</span></li>
            <li><strong>内容解析</strong><span>提取正文并保留页码与段落位置</span></li>
            <li><strong>规则切分</strong><span>按条款和主题生成可引用片段</span></li>
            <li><strong>索引发布</strong><span>审核通过后供 Agent 检索</span></li>
          </ol>
        </aside>
      </section>

      <section className="resource-library">
        <div className="section-heading">
          <div><p className="eyebrow">RESOURCE LIBRARY</p><h2>规则文件</h2></div>
          <span>{resources.length} 个文件</span>
        </div>

        {resources.length === 0 ? (
          <div className="card resource-empty">
            <strong>还没有规则文件</strong>
            <p>上传采购制度、技术规范或评审说明后，可在这里预览和管理。</p>
          </div>
        ) : (
          <div className="resource-table-wrap">
            <table className="resource-table">
              <thead><tr><th>文件</th><th>分类</th><th>状态</th><th>添加时间</th><th aria-label="操作" /></tr></thead>
              <tbody>
                {resources.map((resource) => (
                  <tr key={resource.id}>
                    <td>
                      <span className="resource-file-type">{fileExtension(resource.file.name)}</span>
                      <span><strong>{resource.file.name}</strong><small>{formatBytes(resource.file.size)}</small></span>
                    </td>
                    <td>{resource.category}</td>
                    <td><span className="status-pill status-pending">本地待入库</span></td>
                    <td>{new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(resource.addedAt))}</td>
                    <td>
                      <div className="resource-row-actions">
                        <button
                          type="button"
                          onClick={() => setPreview({ name: resource.file.name, mediaType: resource.file.type, sizeBytes: resource.file.size, file: resource.file })}
                        >预览</button>
                        <button type="button" onClick={() => setResources((current) => current.filter((item) => item.id !== resource.id))}>移除</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="session-note">原型中的文件仅保存在当前浏览器内存中；刷新页面会清空，不代表已经进入后端规则库。</p>
      </section>

      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}

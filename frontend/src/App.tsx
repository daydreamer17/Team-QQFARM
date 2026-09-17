import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { AuditPage } from './pages/AuditPage'
import { CompliancePage } from './pages/CompliancePage'
import { DecisionPage } from './pages/DecisionPage'
import { NewTaskPage } from './pages/NewTaskPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { OverviewPage } from './pages/OverviewPage'
import { PolicyImportPage } from './pages/PolicyImportPage'
import { QuoteUploadPage } from './pages/QuoteUploadPage'
import { ResourcePage } from './pages/ResourcePage'
import { ResultPage } from './pages/ResultPage'
import { SummaryPage } from './pages/SummaryPage'
import { TaskPage } from './pages/TaskPage'
import './App.css'
import './styles/blueprint.css'

function LegacyTaskReviewRedirect() {
  const { taskId = '' } = useParams()
  return <Navigate to={`/tasks/${taskId}/quotes/new`} replace />
}

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path='reviews' element={<Navigate to="/" replace />} />
        <Route path='reviews/:taskId' element={<LegacyTaskReviewRedirect />} />
        <Route path='resources' element={<ResourcePage />} />
        <Route path='resources/policies/:policyImportId' element={<PolicyImportPage />} />
        <Route index element={<OverviewPage />} />
        <Route path="tasks/new" element={<NewTaskPage />} />
        <Route path="tasks/:taskId" element={<TaskPage />} />
        <Route path="tasks/:taskId/quotes/new" element={<QuoteUploadPage />} />
        <Route path="tasks/:taskId/decision" element={<DecisionPage />} />
        <Route path="tasks/:taskId/compliance" element={<CompliancePage />} />
        <Route path="tasks/:taskId/summary" element={<SummaryPage />} />
        <Route path="tasks/:taskId/audit" element={<AuditPage />} />
        <Route path="tasks/:taskId/results/:resultId" element={<ResultPage />} />
        <Route path="404" element={<NotFoundPage />} />
        <Route path="*" element={<Navigate to="/404" replace />} />
      </Route>
    </Routes>
  )
}

export default App

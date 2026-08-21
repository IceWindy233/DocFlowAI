import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { UserLayout } from './components/UserLayout'
import { ConfigurationPage } from './pages/ConfigurationPage'
import { DashboardPage } from './pages/DashboardPage'
import { DocumentReviewPage } from './pages/DocumentReviewPage'
import { DocumentsPage } from './pages/DocumentsPage'
import { DraftWorkbenchPage } from './pages/DraftWorkbenchPage'
import { IngestionPage } from './pages/IngestionPage'
import { PublicationsPage } from './pages/PublicationsPage'
import { QaEvaluationPage } from './pages/QaEvaluationPage'
import { ReviewsPage } from './pages/ReviewsPage'
import { RetrievalPage } from './pages/RetrievalPage'
import { WorkflowRunsPage } from './pages/WorkflowRunsPage'
import { UserHomePage } from './pages/UserHomePage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<UserLayout />}>
        <Route index element={<UserHomePage />} />
        <Route path="qa" element={<RetrievalPage userMode />} />
        <Route path="review" element={<DocumentReviewPage userMode />} />
        <Route path="draft" element={<DraftWorkbenchPage userMode />} />
      </Route>
      <Route path="/admin" element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="ingestion" element={<IngestionPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="drafts" element={<DraftWorkbenchPage />} />
        <Route path="document-review" element={<DocumentReviewPage />} />
        <Route path="retrieval" element={<RetrievalPage />} />
        <Route path="qa-evaluation" element={<QaEvaluationPage />} />
        <Route path="workflows" element={<WorkflowRunsPage />} />
        <Route path="publications" element={<PublicationsPage />} />
        <Route path="reviews" element={<ReviewsPage />} />
        <Route path="configuration" element={<ConfigurationPage />} />
      </Route>
      <Route path="/retrieval" element={<Navigate to="/admin/retrieval" replace />} />
      <Route path="/document-review" element={<Navigate to="/admin/document-review" replace />} />
      <Route path="/drafts" element={<Navigate to="/admin/drafts" replace />} />
      <Route path="/ingestion" element={<Navigate to="/admin/ingestion" replace />} />
      <Route path="/documents" element={<Navigate to="/admin/documents" replace />} />
      <Route path="/qa-evaluation" element={<Navigate to="/admin/qa-evaluation" replace />} />
      <Route path="/workflows" element={<Navigate to="/admin/workflows" replace />} />
      <Route path="/publications" element={<Navigate to="/admin/publications" replace />} />
      <Route path="/reviews" element={<Navigate to="/admin/reviews" replace />} />
      <Route path="/configuration" element={<Navigate to="/admin/configuration" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

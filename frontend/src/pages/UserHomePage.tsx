import { ArrowRight, BookOpenCheck, CheckCircle2, FileCheck2, FilePenLine, ScanSearch, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'

const capabilities = [
  {
    to: '/qa',
    icon: ScanSearch,
    eyebrow: 'KNOWLEDGE QA',
    title: '知识问答',
    description: '在已发布的公文知识库中检索事实、表格和扫描件，生成带页码引用的回答。',
    points: ['混合文本与视觉检索', '证据不足时安全拒答', '每项结论可回看原始页面'],
    action: '开始提问',
  },
  {
    to: '/review',
    icon: FileCheck2,
    eyebrow: 'DOCUMENT REVIEW',
    title: '公文审核',
    description: '检查结构、格式、事实一致性、语言和敏感信息，逐条采纳或驳回建议。',
    points: ['规则与语义联合审核', '意见定位及依据说明', '生成修订稿和审核报告'],
    action: '审核公文',
  },
  {
    to: '/draft',
    icon: FilePenLine,
    eyebrow: 'DOCUMENT DRAFTING',
    title: '公文撰写',
    description: '根据事项信息检索历史案例，确认提纲后生成经过事实校验的请示或函。',
    points: ['关键信息缺失门禁', '提纲确认和版本管理', '事实复验与 DOCX 导出'],
    action: '创建公文',
  },
]

export function UserHomePage() {
  return <div className="user-home">
    <section className="user-hero">
      <div className="user-hero-copy">
        <span className="user-kicker"><Sparkles size={14} />中文公文智能工作台</span>
        <h1>让每一份公文<br />都有依据、可检查、可追溯</h1>
        <p>面向日常办公的三个核心能力：从知识库查询事实，审核待发文稿，根据真实材料起草公文。</p>
        <div className="user-hero-actions"><Link className="button primary" to="/qa">从知识问答开始<ArrowRight size={16} /></Link><Link className="button" to="/draft">新建公文</Link></div>
      </div>
      <div className="user-trust-card">
        <BookOpenCheck size={24} />
        <div><b>当前知识库已发布</b><p>回答只使用已发布索引；正文事实与引用页面会在工作流中再次校验。</p></div>
        <span><i />服务就绪</span>
      </div>
    </section>
    <section className="user-capabilities">
      <div className="user-section-title"><span>核心功能</span><h2>选择你现在要完成的工作</h2></div>
      <div className="user-capability-grid">{capabilities.map(({ to, icon: Icon, eyebrow, title, description, points, action }) => <Link to={to} key={to} className="user-capability-card">
        <div className="user-card-icon"><Icon size={24} /></div><small>{eyebrow}</small><h3>{title}</h3><p>{description}</p>
        <ul>{points.map((point) => <li key={point}><CheckCircle2 size={13} />{point}</li>)}</ul>
        <b>{action}<ArrowRight size={15} /></b>
      </Link>)}</div>
    </section>
  </div>
}

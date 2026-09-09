import React, { useState, useRef, useEffect } from 'react';
import { Bot, User, FileText, Plus, Trash2, MessageSquare, Menu, X } from 'lucide-react';
import ChatInput from './ChatInput';
import { agentsApi, chatApi, ragApi } from '../../lib/api';

const SUGGESTIONS = [
  'What are the key requirements in this project?',
  'Summarize the uploaded documents',
  'What is still missing or unclear?',
];

const cleanInlineMarkdown = (value) => value
  .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
  .replace(/`([^`]+)`/g, '$1')
  .replace(/\*\*([^*]+)\*\*/g, '$1')
  .replace(/__([^_]+)__/g, '$1')
  .replace(/[*_~]/g, '');

const StructuredResponse = ({ text }) => {
  const lines = String(text || '').split(/\r?\n/);
  return (
    <div className="space-y-2 leading-6">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={index} className="h-1" />;
        if (/^#{1,6}\s+/.test(trimmed)) {
          const heading = trimmed.replace(/^#{1,6}\s+/, '');
          return <h4 key={index} className="pt-2 text-sm font-semibold text-gray-100">{cleanInlineMarkdown(heading)}</h4>;
        }
        if (/^[-*+]\s+/.test(trimmed)) {
          return <li key={index} className="ml-5 list-disc pl-1">{cleanInlineMarkdown(trimmed.replace(/^[-*+]\s+/, ''))}</li>;
        }
        if (/^\d+[.)]\s+/.test(trimmed)) {
          return <li key={index} className="ml-5 list-decimal pl-1">{cleanInlineMarkdown(trimmed.replace(/^\d+[.)]\s+/, ''))}</li>;
        }
        return <p key={index}>{cleanInlineMarkdown(trimmed)}</p>;
      })}
    </div>
  );
};

const ChatPanel = ({ projectId, mode = 'query' }) => {
  const [messages, setMessages] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  useEffect(() => {
    let cancelled = false;
    setHistoryLoading(true);
    setMessages([]);
    setSessionId(null);
    chatApi.sessions(projectId, mode)
    .then((items) => {
      if (cancelled) return;
      setSessions(items);
      return [];
    })
      .then((items) => {
        if (!cancelled && items) setMessages(items.map((item) => ({
          id: item.message_id,
          text: item.content,
          sender: item.role === 'user' ? 'user' : 'bot',
          sources: item.sources || [],
        })));
      })
      .catch(() => {
        if (!cancelled) setSessions([]);
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => { cancelled = true; };
  }, [projectId, mode]);

  const selectSession = async (id) => {
    setSessionId(id);
    setIsTyping(true);
    try {
      const items = await chatApi.messages(id);
      setMessages(items.map((item) => ({
        id: item.message_id,
        text: item.content,
        sender: item.role === 'user' ? 'user' : 'bot',
        sources: item.sources || [],
      })));
    } catch (err) {
      setMessages([{ id: `${Date.now()}-err`, text: err.message, sender: 'bot', isError: true }]);
    } finally {
      setIsTyping(false);
    }
  };

  const startNewChat = () => {
    setSessionId(null);
    setMessages([]);
    setHistoryOpen(false);
  };

  const deleteSession = async (id) => {
    await chatApi.removeSession(id);
    const next = sessions.filter((session) => session.session_id !== id);
    setSessions(next);
    if (id === sessionId) {
      setSessionId(null);
      setMessages([]);
    }
  };

  const handleSend = async (text) => {
    const userMsg = { id: Date.now().toString(), text, sender: 'user' };
    setMessages((prev) => [...prev, userMsg]);
    setIsTyping(true);

    try {
      let activeSessionId = sessionId;
      if (!activeSessionId) {
        const session = await chatApi.createSession({
          project_id: projectId || null,
          mode,
          title: text.slice(0, 80),
        });
        activeSessionId = session.session_id;
        setSessionId(activeSessionId);
        setSessions((prev) => [session, ...prev]);
      }
      await chatApi.addMessage(activeSessionId, { role: 'user', content: text });
      let result;
      if (mode === 'rag') {
        const sources = (await ragApi.search(text, projectId)) || [];
        result = {
          answer: `RAG retrieved ${sources.length} authorized source matches for this question.`,
          sources,
        };
      } else {
        result = await ragApi.ask(text, projectId);
      }
      const botId = `${Date.now()}-bot`;
      const botMsg = {
        id: botId,
        text: result.answer,
        sender: 'bot',
        sources: result.sources || [],
        followups: [],
      };
      setMessages((prev) => [...prev, botMsg]);
      await chatApi.addMessage(activeSessionId, {
        role: 'assistant',
        content: result.answer,
        sources: result.sources || [],
      });
      if (mode !== 'rag') {
        agentsApi.followups(text, projectId).then((followupResult) => {
          const followups = followupResult.followup_questions || [];
          setMessages((prev) => prev.map((message) => message.id === botId ? { ...message, followups } : message));
        }).catch(() => {
          // Follow-ups are optional when the generation provider is rate-limited.
        });
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { id: `${Date.now()}-err`, text: `Something went wrong: ${err.message}`, sender: 'bot', isError: true },
      ]);
    } finally {
      setIsTyping(false);
    }
  };

  return (
    <div className="relative isolate flex flex-col overflow-hidden bg-background">
      {historyOpen && (
        <button
          type="button"
          aria-label="Close chat history"
          onClick={() => setHistoryOpen(false)}
          className="absolute inset-x-0 bottom-0 top-[84px] z-20 cursor-default bg-black/30"
        />
      )}
      <aside className={`absolute right-0 top-[84px] bottom-0 z-30 flex w-72 max-w-[85%] flex-col border-l border-border bg-surface shadow-2xl transition-transform duration-200 ${historyOpen ? 'translate-x-0' : 'translate-x-full'}`}>
        <div className="flex items-center justify-between border-b border-border/50 p-4">
          <h3 className="text-sm font-semibold text-gray-200">Chat history</h3>
          <button type="button" onClick={() => setHistoryOpen(false)} className="text-gray-400 hover:text-gray-100" aria-label="Close chat history">
            <X size={17} />
          </button>
        </div>
        <button type="button" onClick={startNewChat} className="m-3 flex items-center justify-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm text-gray-200 hover:border-primary/50">
          <Plus size={15} /> New conversation
        </button>
        <div className="px-2 space-y-1">
          {historyLoading && <p className="px-2 py-3 text-xs text-gray-500">Loading history...</p>}
          {!historyLoading && sessions.length === 0 && <p className="px-2 py-3 text-xs text-gray-500">No previous chats</p>}
          {sessions.map((session) => (
            <div key={session.session_id} className={`group flex items-center gap-1 rounded-lg ${session.session_id === sessionId ? 'bg-primary/15' : 'hover:bg-background'}`}>
              <button type="button" onClick={() => selectSession(session.session_id)} className="min-w-0 flex-1 px-2 py-2 text-left text-xs text-gray-300">
                <span className="flex items-center gap-1.5"><MessageSquare size={12} className="shrink-0" /><span className="truncate">{session.title}</span></span>
              </button>
              <button type="button" onClick={() => deleteSession(session.session_id)} className="mr-1 hidden group-hover:block text-gray-500 hover:text-red-400" aria-label="Delete chat">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      </aside>
      <div className="relative z-40 p-4 border-b border-border/50 bg-surface">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-100 flex items-center gap-2">
            <Bot size={20} className="text-primary" />
            {mode === 'rag' ? 'RAG Retrieval Agent' : 'General Query Agent'}
          </h2>
          <div className="flex items-center gap-3">
            <span className="text-[10px] uppercase tracking-widest text-primary font-bold">Live</span>
            <button
              type="button"
              onClick={() => setHistoryOpen((open) => !open)}
              className="cursor-pointer text-gray-400 transition-colors hover:text-primary-light"
              aria-label={historyOpen ? 'Close chat history' : 'Open chat history'}
              aria-expanded={historyOpen}
              title="Chat history"
            >
              {historyOpen ? <X size={21} /> : <Menu size={21} />}
            </button>
          </div>
        </div>
        <p className="text-sm text-gray-400 mt-1">
          {mode === 'rag'
            ? 'Search your authorized project knowledge base and inspect the matching source chunks.'
            : 'Gemini scans every stage, document, gap, and workflow item in this project to answer your request.'}
        </p>
      </div>

      <div className="p-4 space-y-6">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center max-w-md mx-auto">
            <Bot size={48} className="text-primary/20 mb-4" />
            <h3 className="text-gray-200 font-medium mb-6">How can I help you today?</h3>
            <div className="flex flex-col gap-2 w-full">
              {SUGGESTIONS.map((suggestion, idx) => (
                <button
                  key={idx}
                  onClick={() => handleSend(suggestion)}
                  className="text-sm text-left p-3 rounded-lg border border-border bg-surface hover:border-primary/50 hover:bg-surface-hover transition-colors text-gray-300"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-6">
            {messages.map((msg) => (
              <div key={msg.id} className={`flex gap-3 ${msg.sender === 'user' ? 'flex-row-reverse' : ''}`}>
                <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${msg.sender === 'user' ? 'bg-primary/20 text-primary-light' : 'bg-surface border border-border text-gray-400'}`}>
                  {msg.sender === 'user' ? <User size={16} /> : <Bot size={16} />}
                </div>
                <div className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ${msg.sender === 'user' ? 'bg-primary text-white rounded-tr-sm' : msg.isError ? 'bg-red-500/10 border border-red-500/30 text-red-300 rounded-tl-sm' : 'bg-surface border border-border text-gray-200 rounded-tl-sm'}`}>
                  <StructuredResponse text={msg.text} />
                  {msg.sources && msg.sources.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-border/50 space-y-1.5">
                      <p className="text-xs text-gray-500 uppercase tracking-wide">Sources</p>
                      {msg.sources.map((source, idx) => (
                        <div key={idx} className="flex items-start gap-1.5 text-xs text-gray-400">
                          <FileText size={12} className="mt-0.5 shrink-0" />
                          <span className="truncate">{source.section_title || source.document_id}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {msg.followups && msg.followups.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-border/50 space-y-1.5">
                      <p className="text-xs text-gray-500 uppercase tracking-wide">Continue exploring</p>
                      {msg.followups.map((followup) => (
                        <button key={followup} type="button" onClick={() => handleSend(followup)} className="block w-full text-left text-xs text-primary-light hover:text-primary transition-colors">
                          {followup}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {isTyping && (
              <div className="flex gap-3">
                <div className="w-8 h-8 rounded-full bg-surface border border-border text-gray-400 flex items-center justify-center shrink-0">
                  <Bot size={16} />
                </div>
                <div className="bg-surface border border-border rounded-2xl rounded-tl-sm px-4 py-3 flex items-center gap-1">
                  <div className="w-1.5 h-1.5 bg-gray-500 rounded-full animate-bounce [animation-delay:-0.3s]"></div>
                  <div className="w-1.5 h-1.5 bg-gray-500 rounded-full animate-bounce [animation-delay:-0.15s]"></div>
                  <div className="w-1.5 h-1.5 bg-gray-500 rounded-full animate-bounce"></div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      <ChatInput onSend={handleSend} disabled={isTyping} />
    </div>
  );
};

export default ChatPanel;

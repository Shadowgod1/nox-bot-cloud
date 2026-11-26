import { Button } from '../../components/ui'
import { TestChat } from '../TestChat/TestChat'
import { StepHeader } from './StepHeader'

/** Wizard step "Проверьте бота": the test chat right after the bot is set up, before any live number. */
export function TestBotStep({ ready, onDone }: { ready: boolean; onDone: () => void }) {
  return (
    <>
      <StepHeader
        title="Проверьте бота"
        text="Напишите как клиентка: спросите цену, мастера, адрес. Бот ответит на ваших настройках — клиентам ничего не уходит, WhatsApp для проверки не нужен."
      />
      {ready ? (
        <TestChat embedded />
      ) : (
        <p className="rounded-[9px] border border-dashed border-line-strong px-4 py-5 text-sm text-ink-2">
          Бот ещё не настроен, проверять пока нечего. Настроить его можно в разделе «Настройки бота», а проверить — в «Тестовом чате».
        </p>
      )}
      <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-line pt-5">
        <Button variant="primary" onClick={onDone}>
          {ready ? 'Всё устраивает, дальше' : 'Дальше'}
        </Button>
        {ready && <span className="text-[13px] text-ink-2">Ответы можно поправить потом в «Настройках бота» и «Услугах и мастерах».</span>}
      </div>
    </>
  )
}

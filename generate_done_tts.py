from gtts import gTTS

tts = gTTS("I am done playing requested songs.", lang="en")
tts.save("done.mp3")
print("done.mp3 generated!")

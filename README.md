# AnythingLLM-F5tts

![image alt](https://github.com/TuonoMindCode/AnythingLLM-F5tts/blob/77a6b833dcf79a3340d80d1b67264049914da4df/anythingf5tts.PNG)



install windows and linux
-------------------------

conda create -n anything python=3.10

conda activate anything

pip install -r requirements.txt

python anythingllm_messages.py

next time to start the app in the anything directory:
conda activate anything

python anythingllm_messages.py

first time you start the app it creates "config_f5tts_any.txt"
exit and start anythingllm and create "developer api"
copy the api key to "config_f5tts_any.txt" api key field.
now start f5 tts use a referens audio and generate text in f5 tts
in the console window for f5 tts look at the field ref_text 
copy that text to a file same name as the referens audio but with .txt
example "me.mp3" and "me.txt" create a directory inside anything "referenc"
copy both me.mp3 and me.txt to "referenc" 
you can have as many as you like (i hope, just tried two), you can chose in the setup witch one to use.

how dos the app work?
---------------------
when you write something in anythingllm dos the response go to the app "anythingllm_messages.py"?

answer: No,  The app "anythingllm_messages.py" checks for new messages using the AnythingLMM 
develop API every 5 seconds (by default) but can be adjusted between 1 to 10 second, 
it check the 20 newest messages.

"anythingllm_messages.py" could prevent the computer to go to sleep because it checks continuously for new message in the background.

Does The App Play F5 TTS Audio Automatically?

answer: Yes, it does. Once generated, the audio will play automatically. In the app you can use python own audio player or you can 
chose the default media player.

how fast is it?

answer: i use nvidia rtx 2080 8 gig ram, a single sentence takes approximately 2-4 seconds.. Processing 300 words may take around 25 seconds.

linux tips?

answer: To ensure optimal performance, start F5-TTS first. The app requires about 2GB of GPU VRAM. If you initiate AnythingLMM first, 
it’s likely that the GPU memory will be allocated to it, causing F5 TTS to run slowly without sufficient GPU memory.

Linux issue during configuration?

answer: Within the configuration menu, input numbers do not seem to produce any visible results. This can be confusing, but it is important to note that the app will still work as expected if you proceed.

Enter the required number for your desired setting.
Press Enter to confirm your selection and continue.

Credits and AI-Generated Code Disclaimer
Some parts of this project were generated using an AI tool. The majority of the code was developed with the assistance of ChatGPT (or other AI tools). This process helped streamline initial development but may require additional human refinement and optimization to achieve desired functionality.


email: tuonomindcode@bahnhof.se

<a href='https://ko-fi.com/T6T21EVI5G' target='_blank'><img height='36' style='border:0px;height:36px;' src='https://storage.ko-fi.com/cdn/kofi5.png?v=6' border='0' alt='Buy Me a Coffee at ko-fi.com' /></a>
